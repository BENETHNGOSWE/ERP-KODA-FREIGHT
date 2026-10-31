"""Kodafreight Customer Statement - who owes what, from real money.

This is the report that was rebuilt in Laravel (`FinancialreportController`): the old
version summed invoice totals next to a *status* that a human had typed, so the numbers
never matched the cash. In ERPNext the allocations are real:

    Total Due   = SUM(grand_total)              of submitted invoices
    Received    = SUM(grand_total - outstanding_amount)
    Balance     = SUM(outstanding_amount)

`outstanding_amount` is maintained by Payment Entry, so "Received" is money that was
actually allocated against an invoice - never an opinion.
"""

import frappe
from frappe import _
from frappe.utils import flt

from kodafreight.utils.tenant import get_user_client, is_platform_admin


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    return columns, data, None, chart


def get_columns():
    return [
        {
            "label": _("Customer"),
            "fieldname": "customer",
            "fieldtype": "Link",
            "options": "Customer",
            "width": 160,
        },
        {"label": _("Customer Name"), "fieldname": "customer_name", "fieldtype": "Data", "width": 220},
        {"label": _("Invoices"), "fieldname": "invoice_count", "fieldtype": "Int", "width": 90},
        {"label": _("Total Due"), "fieldname": "total_due", "fieldtype": "Currency", "width": 150},
        {"label": _("Total Received"), "fieldname": "total_received", "fieldtype": "Currency", "width": 150},
        {
            "label": _("Balance Outstanding"),
            "fieldname": "balance",
            "fieldtype": "Currency",
            "width": 160,
        },
    ]


def get_conditions(filters):
    conditions = ["si.docstatus = 1"]
    values = {}

    if filters.get("from_date"):
        conditions.append("si.posting_date >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("si.posting_date <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("customer"):
        conditions.append("si.customer = %(customer)s")
        values["customer"] = filters.customer
    if filters.get("only_outstanding"):
        conditions.append("si.outstanding_amount > 0")

    # Tenant isolation: a tenant user may only ever see its own customers.
    if not is_platform_admin():
        client = get_user_client()
        conditions.append("c.kf_client = %(client)s")
        values["client"] = client or "__no_client__"

    return " and ".join(conditions), values


def get_data(filters):
    where, values = get_conditions(filters)
    return frappe.db.sql(
        f"""
        select
            si.customer                                   as customer,
            max(si.customer_name)                         as customer_name,
            count(si.name)                                as invoice_count,
            sum(si.grand_total)                           as total_due,
            sum(si.grand_total - si.outstanding_amount)   as total_received,
            sum(si.outstanding_amount)                    as balance
        from `tabSales Invoice` si
        left join `tabCustomer` c on c.name = si.customer
        where {where}
        group by si.customer
        order by balance desc, si.customer asc
        """,
        values,
        as_dict=True,
    )


def get_chart(data):
    rows = [row for row in data if flt(row.get("balance")) > 0][:10]
    if not rows:
        return None
    return {
        "data": {
            "labels": [row["customer_name"] or row["customer"] for row in rows],
            "datasets": [{"name": _("Balance Outstanding"), "values": [flt(r["balance"]) for r in rows]}],
        },
        "type": "bar",
        "height": 300,
    }
