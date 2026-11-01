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
    """Sales Invoice changed -> refresh the Cargo Load(s) that created it."""
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
