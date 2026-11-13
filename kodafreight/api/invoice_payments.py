"""Read-only helpers for the Sales Invoice "Payments Received" panel.

The Laravel invoice screen listed every payment against the invoice. In ERPNext the money
lives in Payment Entries, so the list is always *derived* here - never stored - which means
it cannot drift, and it works even for an already-submitted invoice (child tables of a
submitted document cannot be edited).
"""

import frappe
from frappe.utils import flt

from kodafreight.permissions import sales_invoice_has_permission


def get_payments(invoice):
    """Return the payments allocated to one Sales Invoice."""
    if not invoice or not frappe.db.exists("Sales Invoice", invoice):
        return []
    return frappe.db.sql(
        """
        select pe.name            as payment_entry,
               pe.posting_date    as payment_date,
               per.allocated_amount as amount,
               coalesce(pe.mode_of_payment, '') as mode_of_payment,
               coalesce(pe.reference_no, '')    as reference_no,
               coalesce(pe.owner, '')           as received_by,
               coalesce(pe.remarks, '')         as notes
        from `tabPayment Entry Reference` per
        inner join `tabPayment Entry` pe on pe.name = per.parent
        where per.reference_doctype = 'Sales Invoice'
          and per.reference_name = %s
          and pe.docstatus = 1
        order by pe.posting_date asc, pe.name asc
        """,
        (invoice,),
        as_dict=True,
    )


@frappe.whitelist()
def for_invoice(invoice):
    """Whitelisted: used by the invoice form to refresh the payments table."""
    doc = frappe.get_doc("Sales Invoice", invoice)
    if not sales_invoice_has_permission(doc):
        frappe.throw(frappe._("Not permitted."), frappe.PermissionError)
    rows = get_payments(invoice)
    for row in rows:
        try:
            row["received_by"] = (
                frappe.db.get_value("User", row["received_by"], "full_name")
                or row["received_by"]
            )
        except Exception:
            pass
    return rows


@frappe.whitelist()
def unpaid_invoices(customer):
    """Invoices of this customer that still have a balance - for the payment form."""
    if not customer:
        return []
    return frappe.get_all(
        "Sales Invoice",
        filters={"customer": customer, "docstatus": 1, "outstanding_amount": [">", 0]},
        fields=["name", "posting_date", "grand_total", "outstanding_amount", "currency"],
        order_by="posting_date desc",
        limit=25,
    )
