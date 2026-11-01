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


def _fill_company_context(doc, company=None):
    """Resolve letterhead details in Python so the Jinja templates stay dumb."""
    from kodafreight.services.freight import default_company

    company = company or doc.get("company")
    if not company:
        try:
            company = default_company()
        except Exception:
            company = None
    if not company or not frappe.db.exists("Company", company):
        return
    row = frappe.db.get_value(
        "Company", company, ["company_name", "phone_no", "tax_id"], as_dict=True
    ) or {}
    doc.kf_company_name = row.get("company_name") or company
    doc.kf_company_phone = row.get("phone_no") or ""
    doc.kf_company_tin = row.get("tax_id") or ""
    address = frappe.db.get_value("Company", company, "company_address") or ""
    doc.kf_company_address = address or ""


def _fill_customer_phone(doc):
    customer = doc.get("customer")
    if not customer:
        return
    doc.kf_customer_phone = (
        frappe.db.get_value("Customer", customer, "mobile_no") or ""
    )


def set_freight_print_context(doc, method=None):
    """before_print for Cargo Load / Release Order / Delivery Order."""
    _fill_company_context(doc)
    _fill_customer_phone(doc)


def set_invoice_print_context(doc, method=None):
    """Prepare everything the Final Invoice PDF needs, before Jinja renders it.

    Print formats run in a sandboxed Jinja environment where database lookups are
    unreliable (that is what produced `417: There was an error building this page`), so the
    company letterhead, TIN and bank details are resolved here - in normal Python - and the
    template only prints plain fields.
    """
    if not frappe.db.exists("Sales Invoice", doc.name or ""):
        return

    company = doc.get("company")
    _fill_company_context(doc, company)
    _fill_customer_phone(doc)

    banks = frappe.get_all(
        "Bank Account",
        filters={"is_company_account": 1, "company": company} if company else {},
        fields=["bank", "account_name", "account_no", "iban"],
        limit=3,
    )
    lines = []
    for bank in banks:
        lines.append(
            "Bank Name: {0}\nAcc Name: {1}\nAcc No: {2}".format(
                bank.get("bank") or "-",
                bank.get("account_name") or "-",
                bank.get("account_no") or bank.get("iban") or "-",
            )
        )
    doc.kf_payment_info = "\n\n".join(lines)

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
