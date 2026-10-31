# Copyright (c) 2026, Kodafreight and contributors
"""Running credit statement per client (allocations, deductions, refunds, adjustments)."""

import frappe
from frappe import _

from kodafreight.utils.tenant import get_user_client, is_platform_admin


def execute(filters=None):
    filters = frappe._dict(filters or {})
    client = get_user_client()
    if client:
        filters.client = client
    elif not is_platform_admin():
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    conditions = ["1 = 1"]
    params = {}
    if filters.get("client"):
        conditions.append("t.client = %(client)s")
        params["client"] = filters.client
    if filters.get("from_date"):
        conditions.append("date(t.posting_datetime) >= %(from_date)s")
        params["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("date(t.posting_datetime) <= %(to_date)s")
        params["to_date"] = filters.to_date

    rows = frappe.db.sql(
        f"""
        select t.name, t.posting_datetime, t.client, t.transaction_type, t.quantity,
               t.balance_after, t.reference_doctype, t.reference_name, t.remarks
        from `tabKodafreight SMS Credit Transaction` t
        where {' and '.join(conditions)}
        order by t.posting_datetime asc, t.creation asc
        """,
        params, as_dict=True,
    )

    for row in rows:
        row["credit"] = row.quantity if row.transaction_type in ("Allocation", "Refund",
                                                                 "Adjustment") else 0
        row["debit"] = row.quantity if row.transaction_type in ("Deduction", "Expiry") else 0

    columns = [
        {"label": _("Date"), "fieldname": "posting_datetime", "fieldtype": "Datetime",
         "width": 160},
        {"label": _("Entry"), "fieldname": "name", "fieldtype": "Link",
         "options": "Kodafreight SMS Credit Transaction", "width": 150},
        {"label": _("Client"), "fieldname": "client", "fieldtype": "Link",
         "options": "Kodafreight Client", "width": 120},
        {"label": _("Type"), "fieldname": "transaction_type", "fieldtype": "Data", "width": 110},
        {"label": _("Credit"), "fieldname": "credit", "fieldtype": "Int", "width": 90},
        {"label": _("Debit"), "fieldname": "debit", "fieldtype": "Int", "width": 90},
        {"label": _("Balance"), "fieldname": "balance_after", "fieldtype": "Int", "width": 100},
        {"label": _("Reference"), "fieldname": "reference_name", "fieldtype": "Data",
         "width": 170},
        {"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 220},
    ]
    return columns, rows
