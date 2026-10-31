"""Freight operations service.

This is the ERPNext translation of the Laravel freight flow:

    Load created  ->  invoice opened  ->  priced as CBM x rate x FX (+ transport)
                  ->  payments recorded (native Payment Entry)
                  ->  Release Order only when nothing is outstanding
                  ->  Delivery Order when the cargo leaves the godown

Deliberate design decisions (each one fixes something the Laravel code got wrong):

* **Money is never invented here.** Amounts live in the ERPNext Sales Invoice /
  Payment Entry ledger, so "Partly Paid" is computed from real allocations instead of
  a status a human could type (Laravel: a hand-edited `status` column).
* **CBM is computed, but the operator can override it.** Laravel allowed a typed
  `load_volume` *and* computed L x W x H, and the two silently disagreed. Here the
  measured volume is always visible and an explicit `use_manual_volume` flag decides
  which one is billable - no silent zero.
* **Zero-value invoices are refused.** A load with no rate and no fee cannot produce a
  meaningless 0.00 bill; it stays "Pending Pricing" until it is priced.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, today

FREIGHT_ITEM = "Freight Charges"
TRANSPORT_ITEM = "Transport Charges"

INVOICE_PENDING = "Pending Pricing"
INVOICE_DRAFT = "Draft Invoice"
INVOICE_INVOICED = "Invoiced"


# --------------------------------------------------------------------------- volume
def compute_volume(length, width, height):
    """Measured volume in cubic metres (Laravel `LoadPackageHelper::createLoad`)."""
    return flt(length) * flt(width) * flt(height)


def billable_volume(doc):
    if doc.get("use_manual_volume"):
        return flt(doc.get("manual_volume"))
    return compute_volume(doc.get("length"), doc.get("width"), doc.get("height"))


def freight_amount(doc):
    """Billable CBM x CBM rate x exchange rate."""
    rate = flt(doc.get("cbm_rate")) * (flt(doc.get("exchange_rate")) or 1)
    return flt(billable_volume(doc)) * rate


def total_amount(doc):
    """Laravel: amount due = total_amount + total_transportfee."""
    return flt(freight_amount(doc)) + flt(doc.get("transport_fee"))


def is_priced(doc):
    """A load is billable as soon as it produces a non-zero amount.

    Measurements are already mandatory (Cargo Load validation), so this only asks whether
    any money has been agreed: a CBM rate for the volume, and/or a transport fee.
    """
    return flt(total_amount(doc)) > 0


def apply_pricing(doc):
    """Recompute every derived money/volume field on a Cargo Load in place."""
    doc.volume_cbm = compute_volume(doc.get("length"), doc.get("width"), doc.get("height"))
    doc.billable_volume = billable_volume(doc)
    doc.freight_amount = freight_amount(doc)
    doc.total_amount = total_amount(doc)
    return doc


def apply_tariff(doc):
    """Prefill rates from the client's default Freight Tariff when they are empty."""
    if flt(doc.get("cbm_rate")):
        return doc
    tariffs = frappe.get_all(
        "Kodafreight Freight Tariff",
        filters={"client": doc.get("client"), "enabled": 1},
        fields=["name", "cbm_rate", "currency", "exchange_rate", "transport_fee", "is_default"],
        order_by="is_default desc, modified desc",
        limit=1,
    )
    if tariffs:
        t = tariffs[0]
        doc.cbm_rate = flt(t.cbm_rate)
        doc.exchange_rate = flt(t.exchange_rate) or 1
        if not flt(doc.get("transport_fee")):
            doc.transport_fee = flt(t.transport_fee)
    return doc


# --------------------------------------------------------------------------- invoice
def default_company():
    company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
        "Global Defaults", "default_company"
    )
    if not company:
        frappe.throw(
            _(
                "No default Company is configured. Set one in Global Defaults before "
                "invoicing freight loads."
            )
        )
    return company


def _require_item(item_code):
    if not frappe.db.exists("Item", item_code):
        frappe.throw(
            _(
                "Item {0} is missing. Run `bench --site <site> migrate` (or create the "
                "item manually) before invoicing freight loads."
            ).format(item_code)
        )
    return item_code


def invoice_rows_for(doc):
    """Sales Invoice item rows for one priced Cargo Load."""
    rows = []
    volume = flt(billable_volume(doc))
    rate = flt(doc.get("cbm_rate")) * (flt(doc.get("exchange_rate")) or 1)
    if volume > 0 and rate > 0:
        rows.append(
            {
                "item_code": _require_item(FREIGHT_ITEM),
                "item_name": _("Freight - {0} CBM").format(flt(volume, 4)),
                "qty": volume,
                "uom": "Cubic Meter",
                "rate": rate,
                "description": doc.get("description") or _("Freight charges"),
                "kf_cargo_load": doc.name,
            }
        )
    if flt(doc.get("transport_fee")) > 0:
        rows.append(
            {
                "item_code": _require_item(TRANSPORT_ITEM),
                "item_name": _("Transport / trucking"),
                "qty": 1,
                "rate": flt(doc.get("transport_fee")),
                "description": doc.get("trucking_number") or _("Transport fee"),
                "kf_cargo_load": doc.name,
            }
        )
    return rows


def sync_sales_invoice(doc, submit=False):
    """Create or refresh the Sales Invoice that bills this Cargo Load.

    Mirrors Laravel's `LoadPackageHelper::generateInvoice()` + `updateInvoiceTotals()`,
    except the invoice only exists once the load is actually priced, and it is always a
    real ERPNext ledger document.
    """
    if not is_priced(doc):
        doc.invoice_status = INVOICE_PENDING
        doc.sales_invoice = None
        return None

    rows = invoice_rows_for(doc)
    if not rows:
        doc.invoice_status = INVOICE_PENDING
        return None

    if doc.get("sales_invoice"):
        invoice = frappe.get_doc("Sales Invoice", doc.sales_invoice)
        if invoice.docstatus == 2:
            frappe.throw(_("Invoice {0} is cancelled. Create a new one.").format(invoice.name))
        if invoice.docstatus == 1:
            doc.invoice_status = invoice_status_label(invoice)
            return invoice
        # Draft: rewrite the rows so a re-price is always reflected.
        invoice.set("items", [])
        for row in rows:
            invoice.append("items", row)
        if invoice.meta.has_field("kf_cargo_load"):
            invoice.kf_cargo_load = doc.name
        invoice.remarks = _("Freight charges for cargo load {0}.").format(doc.name)
        invoice.flags.ignore_permissions = True
        invoice.save()
        doc.invoice_status = INVOICE_DRAFT
        return invoice

    invoice = frappe.new_doc("Sales Invoice")
    invoice.company = default_company()
    invoice.customer = doc.customer
    invoice.posting_date = today()
    invoice.due_date = doc.get("delivery_date") or today()
    invoice.set_posting_time = 1
    invoice.remarks = _("Freight charges for cargo load {0} ({1}).").format(
        doc.name, doc.get("load_no") or doc.name
    )
    for row in rows:
        invoice.append("items", row)
    if invoice.meta.has_field("kf_cargo_load"):
        invoice.kf_cargo_load = doc.name
    invoice.flags.ignore_permissions = True
    invoice.insert()
    doc.sales_invoice = invoice.name
    doc.invoice_status = INVOICE_DRAFT

    if submit:
        invoice.flags.ignore_permissions = True
        invoice.submit()
        doc.invoice_status = invoice_status_label(invoice)
    return invoice


def invoice_status_label(invoice):
    """Label a Sales Invoice the way a freight clerk thinks about it."""
    if invoice.docstatus == 0:
        return INVOICE_DRAFT
    if invoice.docstatus == 2:
        return "Cancelled"
    outstanding = flt(invoice.outstanding_amount)
    if outstanding <= 0:
        return "Paid"
    if outstanding < flt(invoice.grand_total):
        return "Partly Paid"
    return INVOICE_INVOICED


def refresh_load_from_invoice(load_name):
    """Push the invoice's payment state back onto the Cargo Load row."""
    invoice_name = frappe.db.get_value("Kodafreight Cargo Load", load_name, "sales_invoice")
    if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
        return
    invoice = frappe.get_doc("Sales Invoice", invoice_name)
    frappe.db.set_value(
        "Kodafreight Cargo Load",
        load_name,
        "invoice_status",
        invoice_status_label(invoice),
        update_modified=False,
    )


# --------------------------------------------------------------------------- payment gate
def invoice_payment_state(invoice_name):
    if not invoice_name:
        frappe.throw(_("A Sales Invoice is required."))
    invoice = frappe.get_doc("Sales Invoice", invoice_name)
    return {
        "name": invoice.name,
        "customer": invoice.customer,
        "docstatus": invoice.docstatus,
        "grand_total": flt(invoice.grand_total),
        "outstanding_amount": flt(invoice.outstanding_amount),
        "paid": invoice.docstatus == 1 and flt(invoice.outstanding_amount) <= 0,
    }


def assert_invoice_paid(invoice_name, allow_partial=False):
    """The Release Order gate.

    Laravel hard-coded `if ($invoice->status != 1)` inside the route that printed the
    Release Order. Here the rule lives on the document that grants the release, so it
    cannot be bypassed by hitting a print URL directly.
    """
    state = invoice_payment_state(invoice_name)
    if state["docstatus"] != 1:
        frappe.throw(
            _("Invoice {0} is not submitted yet. Submit it before releasing cargo.").format(
                state["name"]
            )
        )
    if not state["paid"] and not allow_partial:
        frappe.throw(
            _(
                "Invoice {0} still has an outstanding balance of {1}. Cargo cannot be "
                "released until the invoice is fully paid."
            ).format(state["name"], "{:,.2f}".format(state["outstanding_amount"])),
            title=_("Invoice not fully paid"),
        )
    return state


# --------------------------------------------------------------------------- loads
def loads_for_invoice(invoice_name):
    return frappe.get_all(
        "Kodafreight Cargo Load",
        filters={"sales_invoice": invoice_name, "docstatus": 1},
        pluck="name",
    )


def set_load_status(load_names, status):
    for name in load_names:
        frappe.db.set_value(
            "Kodafreight Cargo Load", name, "shipment_status", status, update_modified=False
        )


def loads_in_container(container_name, exclude=None):
    """Cargo Loads currently submitted inside a container."""
    filters = {"container": container_name, "docstatus": 1}
    if exclude:
        filters["name"] = ["!=", exclude]
    return frappe.get_all("Kodafreight Cargo Load", filters=filters, pluck="name")


def container_of(load_name):
    return frappe.db.get_value("Kodafreight Cargo Load", load_name, "container")


# --------------------------------------------------------------------------- merging
def parse_invoice_names(invoice_names):
    """Accept a JSON array (what the list view posts), a comma list or a newline list."""
    if isinstance(invoice_names, str):
        text = invoice_names.strip()
        if text.startswith("["):
            try:
                invoice_names = json.loads(text)
            except ValueError:
                frappe.throw(_("Could not read the selected invoices."))
        else:
            invoice_names = text.replace(",", "\n").split("\n")
    names = [str(name).strip() for name in invoice_names if name and str(name).strip()]
    if not names:
        frappe.throw(_("Select at least one invoice to merge."))
    return names


@frappe.whitelist()
def download_merged_invoice(invoice_names):
    """Combined bill over several invoices of ONE customer (Laravel `InvoiceMerge`).

    The original invoices are untouched - merging only produces one PDF, exactly like
    the Laravel system.
    """
    names = parse_invoice_names(invoice_names)
    invoices = [frappe.get_doc("Sales Invoice", n) for n in names]

    customers = {inv.customer for inv in invoices}
    if len(customers) > 1:
        frappe.throw(_("All merged invoices must belong to the same customer."))

    client = None
    for inv in invoices:
        inv_client = frappe.db.get_value("Customer", inv.customer, "kf_client")
        if client and inv_client and inv_client != client:
            frappe.throw(_("All merged invoices must belong to the same client."))
        client = client or inv_client

    html = frappe.render_template(
        "templates/print_formats/kodafreight_merged_invoice.html",
        {
            "doc": invoices[0],
            "invoices": invoices,
            "customer": invoices[0].customer,
            "customer_name": invoices[0].customer_name,
            "total_grand": sum(flt(i.grand_total) for i in invoices),
            "total_outstanding": sum(flt(i.outstanding_amount) for i in invoices),
            "invoice_numbers": ", ".join(invoices[i].name for i in range(len(invoices))),
        },
    )
    frappe.local.response.filename = "merged-invoice-{0}.pdf".format(today())
    frappe.local.response.filecontent = frappe.utils.pdf.get_pdf(html)
    frappe.local.response.type = "download"
