"""Kodafreight CBM Record - volume and revenue per cargo load.

Replaces the Laravel "CBM Record" screen: how much volume moved, for which customer,
and how much it was worth.
"""

import frappe
from frappe import _
from frappe.utils import flt

from kodafreight.utils.tenant import get_user_client, is_platform_admin


def execute(filters=None):
    filters = frappe._dict(filters or {})
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": _("Cargo Load"), "fieldname": "name", "fieldtype": "Link",
         "options": "Kodafreight Cargo Load", "width": 150},
        {"label": _("Load No"), "fieldname": "load_no", "fieldtype": "Data", "width": 140},
        {"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 200},
        {"label": _("Container"), "fieldname": "container", "fieldtype": "Link",
         "options": "Kodafreight Container", "width": 140},
        {"label": _("CBM"), "fieldname": "billable_volume", "fieldtype": "Float",
         "precision": 4, "width": 100},
        {"label": _("Weight (kg)"), "fieldname": "weight", "fieldtype": "Float", "width": 110},
        {"label": _("Shipment Status"), "fieldname": "shipment_status", "fieldtype": "Data", "width": 140},
        {"label": _("Invoice"), "fieldname": "sales_invoice", "fieldtype": "Link",
         "options": "Sales Invoice", "width": 150},
        {"label": _("Invoice Status"), "fieldname": "invoice_status", "fieldtype": "Data", "width": 130},
        {"label": _("Total Amount"), "fieldname": "total_amount", "fieldtype": "Currency", "width": 140},
    ]


def get_data(filters):
    conditions = ["cl.docstatus = 1"]
    values = {}
    if filters.get("from_date"):
        conditions.append("cl.transport_date >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("cl.transport_date <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("customer"):
        conditions.append("cl.customer = %(customer)s")
        values["customer"] = filters.customer
    if filters.get("shipment_status"):
        conditions.append("cl.shipment_status = %(shipment_status)s")
        values["shipment_status"] = filters.shipment_status
    if filters.get("container"):
        conditions.append("cl.container = %(container)s")
        values["container"] = filters.container

    if not is_platform_admin():
        conditions.append("cl.client = %(client)s")
        values["client"] = get_user_client() or "__no_client__"

    rows = frappe.db.sql(
        f"""
        select cl.name, cl.load_no, cl.customer_name, cl.container,
               cl.billable_volume, cl.weight, cl.shipment_status,
               cl.sales_invoice, cl.invoice_status, cl.total_amount
        from `tabKodafreight Cargo Load` cl
        where {' and '.join(conditions)}
        order by cl.transport_date desc, cl.name desc
        """,
        values,
        as_dict=True,
    )
    for row in rows:
        row["total_amount"] = flt(row["total_amount"])
    return rows
