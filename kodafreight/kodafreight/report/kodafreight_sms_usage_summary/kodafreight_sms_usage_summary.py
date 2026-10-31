# Copyright (c) 2026, Kodafreight and contributors
"""SMS usage per client / sender / status. Tenant safe: a client only ever sees its own rows."""

import frappe
from frappe import _

from kodafreight.utils.tenant import get_user_client, is_platform_admin


def execute(filters=None):
    filters = frappe._dict(filters or {})
    client = get_user_client()
    if client:
        filters.client = client  # never trust the filter for tenant users
    elif not is_platform_admin():
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    conditions = ["1 = 1"]
    params = {}
    if filters.get("client"):
        conditions.append("m.client = %(client)s")
        params["client"] = filters.client
    if filters.get("from_date"):
        conditions.append("date(m.creation) >= %(from_date)s")
        params["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("date(m.creation) <= %(to_date)s")
        params["to_date"] = filters.to_date
    if filters.get("sender_name"):
        conditions.append("m.sender_name = %(sender_name)s")
        params["sender_name"] = filters.sender_name

    data = frappe.db.sql(
        f"""
        select m.client, m.sender_name,
               count(*) as total,
               sum(case when m.status in ('Sent','Delivered') then 1 else 0 end) as successful,
               sum(case when m.status = 'Delivered' then 1 else 0 end) as delivered,
               sum(case when m.status = 'Failed' then 1 else 0 end) as failed,
               sum(case when m.status in ('Queued','Sending') then 1 else 0 end) as pending,
               coalesce(sum(m.credits_used), 0) as credits
        from `tabKodafreight SMS Message` m
        where {' and '.join(conditions)}
        group by m.client, m.sender_name
        order by total desc
        """,
        params, as_dict=True,
    )

    columns = [
        {"label": _("Client"), "fieldname": "client", "fieldtype": "Link",
         "options": "Kodafreight Client", "width": 160},
        {"label": _("Sender Name"), "fieldname": "sender_name", "fieldtype": "Data", "width": 130},
        {"label": _("Total"), "fieldname": "total", "fieldtype": "Int", "width": 90},
        {"label": _("Successful"), "fieldname": "successful", "fieldtype": "Int", "width": 100},
        {"label": _("Delivered"), "fieldname": "delivered", "fieldtype": "Int", "width": 100},
        {"label": _("Failed"), "fieldname": "failed", "fieldtype": "Int", "width": 90},
        {"label": _("Pending"), "fieldname": "pending", "fieldtype": "Int", "width": 90},
        {"label": _("Credits Used"), "fieldname": "credits", "fieldtype": "Int", "width": 110},
    ]
    chart = {
        "data": {
            "labels": [f"{r.client}/{r.sender_name}" for r in data][:10],
            "datasets": [
                {"name": _("Successful"), "values": [r.successful for r in data][:10]},
                {"name": _("Failed"), "values": [r.failed for r in data][:10]},
            ],
        },
        "type": "bar",
    }
    return columns, data, None, chart
