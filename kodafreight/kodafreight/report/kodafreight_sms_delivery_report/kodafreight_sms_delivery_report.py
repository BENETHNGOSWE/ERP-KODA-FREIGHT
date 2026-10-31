# Copyright (c) 2026, Kodafreight and contributors
"""Message level delivery report with the provider outcome."""

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
    for field in ("client", "status", "sender_name", "campaign"):
        if filters.get(field):
            conditions.append(f"m.{field} = %({field})s")
            params[field] = filters.get(field)
    if filters.get("from_date"):
        conditions.append("date(m.creation) >= %(from_date)s")
        params["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("date(m.creation) <= %(to_date)s")
        params["to_date"] = filters.to_date

    data = frappe.db.sql(
        f"""
        select m.name, m.creation, m.client, m.recipient, m.sender_name, m.status,
               m.parts, m.credits_used, m.sent_at, m.delivered_at, m.retry_count,
               m.error_message, m.provider, m.provider_message_id
        from `tabKodafreight SMS Message` m
        where {' and '.join(conditions)}
        order by m.creation desc
        """,
        params, as_dict=True,
    )

    columns = [
        {"label": _("Message"), "fieldname": "name", "fieldtype": "Link",
         "options": "Kodafreight SMS Message", "width": 160},
        {"label": _("Date"), "fieldname": "creation", "fieldtype": "Datetime", "width": 150},
        {"label": _("Client"), "fieldname": "client", "fieldtype": "Link",
         "options": "Kodafreight Client", "width": 120},
        {"label": _("Recipient"), "fieldname": "recipient", "fieldtype": "Data", "width": 120},
        {"label": _("Sender"), "fieldname": "sender_name", "fieldtype": "Data", "width": 110},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
        {"label": _("Parts"), "fieldname": "parts", "fieldtype": "Int", "width": 70},
        {"label": _("Credits"), "fieldname": "credits_used", "fieldtype": "Int", "width": 80},
        {"label": _("Sent At"), "fieldname": "sent_at", "fieldtype": "Datetime", "width": 150},
        {"label": _("Delivered At"), "fieldname": "delivered_at", "fieldtype": "Datetime",
         "width": 150},
        {"label": _("Retries"), "fieldname": "retry_count", "fieldtype": "Int", "width": 80},
        {"label": _("Error"), "fieldname": "error_message", "fieldtype": "Data", "width": 220},
    ]
    return columns, data
