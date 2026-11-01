"""Event handlers that keep the freight documents and ERPNext's accounting in step.

The Laravel system stored the invoice `status` on the invoice row and never updated it
from payments, so a "Full Paid" label could survive months of unpaid reality. Here the
Sales Invoice is the only source of truth and the Cargo Load simply copies its payment
state whenever the invoice changes - including when a Payment Entry allocates money.
"""

import frappe

from kodafreight.services import freight
from kodafreight.utils.tenant import get_user_client


def sync_cargo_load_status(doc, method=None, *args, **kwargs):
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


def sync_on_payment(doc, method=None, *args, **kwargs):
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


def apply_invoice_pricing(doc, method=None, *args, **kwargs):
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


def _safe(value, default=""):
    return value if value not in (None, "") else default


def _company_address(company):
    """Postal address of a Company.

    ERPNext keeps addresses in the `Address` doctype, linked to the Company through
    `Dynamic Link` - there is no `Company.company_address` column. Asking for that column
    is what produced
        MySQLdb.OperationalError: (1054, "Unknown column 'company_address' in 'SELECT'")
    on a real bench, so the lookup is done against the address table and every step is
    individually guarded.
    """
    try:
        rows = frappe.db.sql(
            """
            select a.address_line1, a.address_line2, a.city, a.state, a.country
            from `tabAddress` a
            inner join `tabDynamic Link` dl
                    on dl.parent = a.name and dl.parenttype = 'Address'
            where dl.link_doctype = 'Company' and dl.link_name = %s
            order by a.is_primary_address desc, a.modified desc
            limit 1
            """,
            (company,),
            as_dict=True,
        )
        if rows:
            row = rows[0]
            parts = [
                row.get("address_line1"),
                row.get("address_line2"),
                row.get("city"),
                row.get("state"),
                row.get("country"),
            ]
            return ", ".join([part for part in parts if part])
    except Exception:
        frappe.log_error(
            title="Kodafreight: company address lookup failed",
            message=frappe.get_traceback(),
        )
    return ""


def _fill_company_context(doc, company=None):
    """Resolve letterhead details in Python so the Jinja templates stay dumb."""
    from kodafreight.services.freight import default_company

    company = doc.get("company") or company
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
    doc.kf_company_name = _safe(row.get("company_name"), company)
    doc.kf_company_phone = _safe(row.get("phone_no"))
    doc.kf_company_tin = _safe(row.get("tax_id"))
    doc.kf_company_address = _company_address(company)


def _fill_customer_phone(doc):
    customer = doc.get("customer")
    if not customer:
        return
    try:
        doc.kf_customer_phone = _safe(frappe.db.get_value("Customer", customer, "mobile_no"))
    except Exception:
        doc.kf_customer_phone = ""


def set_freight_print_context(doc, method=None, print_settings=None, *args, **kwargs):
    """before_print for Cargo Load / Release Order / Delivery Order.

    Frappe calls this as `doc.run_method("before_print", print_settings)`, i.e. with the
    print settings as an extra positional argument - accepting only (doc, method) breaks it
    with `TypeError: takes from 1 to 2 positional arguments but 3 were given`.

    The whole body is failure-tolerant: a broken letterhead detail must degrade to a blank
    line on the paper, never to a 500 page that stops the user from printing at all.
    """
    try:
        _fill_company_context(doc)
        _fill_customer_phone(doc)
    except Exception:
        frappe.log_error(
            title="Kodafreight: print context failed ({0})".format(doc.doctype),
            message=frappe.get_traceback(),
        )


def set_invoice_print_context(doc, method=None, print_settings=None, *args, **kwargs):
    """Prepare everything the Final Invoice PDF needs, before Jinja renders it.

    Same calling convention as `set_freight_print_context` (see the note there).
    

    Print formats run in a sandboxed Jinja environment where database lookups are
    unreliable (that is what produced `417: There was an error building this page`), so the
    company letterhead, TIN and bank details are resolved here - in normal Python - and the
    template only prints plain fields.
    """
    try:
        _build_invoice_print_context(doc)
    except Exception:
        frappe.log_error(
            title="Kodafreight: invoice print context failed",
            message=frappe.get_traceback(),
        )


def _build_invoice_print_context(doc):
    if not frappe.db.exists("Sales Invoice", doc.name or ""):
        return

    company = doc.get("company")
    _fill_company_context(doc, company)
    _fill_customer_phone(doc)

    try:
        banks = frappe.get_all(
            "Bank Account",
            filters={"is_company_account": 1, "company": company} if company else {},
            fields=["bank", "account_name", "account_no", "iban"],
            limit=3,
        )
    except Exception:
        banks = []
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


def stamp_customer_tenant(doc, method=None, *args, **kwargs):
    """Give every Customer a client, so freight data stays inside its tenant.

    Without this, ERPNext's Customer list would show every company's customers - the
    same leak the Laravel `Customer::all()` had.
    """
    if doc.get("kf_client"):
        return
    client = get_user_client()
    if client:
        doc.kf_client = client
