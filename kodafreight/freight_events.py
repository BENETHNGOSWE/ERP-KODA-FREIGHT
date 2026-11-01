"""Event handlers that keep the freight documents and ERPNext's accounting in step.

The Laravel system stored the invoice `status` on the invoice row and never updated it
from payments, so a "Full Paid" label could survive months of unpaid reality. Here the
Sales Invoice is the only source of truth and the Cargo Load simply copies its payment
state whenever the invoice changes - including when a Payment Entry allocates money.
"""

import frappe

from kodafreight.services import freight
from kodafreight.utils.tenant import get_user_client


def sync_cargo_load_status(doc, method=None):
    """Sales Invoice changed -> copy status *and* pricing back onto the Cargo Load.

    This is the "update invoice" step: the clerk types qty / rate / exchange rate /
    transport fee on the invoice, and reopening the cargo load shows those figures.
    """
    if not doc.get("name"):
        return
    loads = frappe.get_all(
        "Cargo Load",
        filters={"sales_invoice": doc.name},
        pluck="name",
    )
    for load_name in loads:
        freight.refresh_load_from_invoice(load_name)


def sync_on_payment(doc, method=None):
    """Payment Entry submitted/cancelled -> the invoices it touched changed state."""
    seen = set()
    for ref in doc.get("references") or []:
        if ref.reference_doctype != "Sales Invoice" or not ref.reference_name:
            continue
        if ref.reference_name in seen:
            continue
        seen.add(ref.reference_name)
        for load_name in frappe.get_all(
            "Cargo Load",
            filters={"sales_invoice": ref.reference_name},
            pluck="name",
        ):
            freight.refresh_load_from_invoice(load_name)


def apply_invoice_pricing(doc, method=None):
    """The Laravel "edit invoice" step, applied to an ERPNext Sales Invoice.

    The clerk types *CBM / Unit Price* and *Exchange Rate* on the invoice; this computes
    the rate of the freight row(s) so totals, tax and accounting all follow ERPNext's own
    maths. Nothing is written into the ledger by hand.
    """
    from frappe.utils import flt

    from kodafreight.services.freight import FREIGHT_ITEM

    price = flt(doc.get("kf_cbm_price"))
    fx = flt(doc.get("kf_exchange_rate")) or 1
    if not price:
        return

    for row in doc.get("items") or []:
        if row.get("item_code") != FREIGHT_ITEM:
            continue
        row.rate = price * fx
        row.amount = flt(row.qty) * row.rate
        row.price_list_rate = row.rate

    # Remember which cargo load produced this invoice, so status flows back.
    if not doc.get("kf_cargo_load"):
        for row in doc.get("items") or []:
            if row.get("kf_cargo_load"):
                doc.kf_cargo_load = row.kf_cargo_load
                break


def stamp_customer_tenant(doc, method=None):
    """Give every Customer a client, so freight data stays inside its tenant.

    Without this, ERPNext's Customer list would show every company's customers - the
    same leak the Laravel `Customer::all()` had.
    """
    if doc.get("kf_client"):
        return
    client = get_user_client()
    if client:
        doc.kf_client = client
