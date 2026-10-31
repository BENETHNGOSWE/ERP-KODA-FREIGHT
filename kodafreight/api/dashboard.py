"""Dashboard data endpoints (platform + client)."""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

from kodafreight.services import credits as credit_service
from kodafreight.utils.tenant import get_user_client, is_platform_admin, resolve_client

SUCCESS_STATES = ("Sent", "Delivered")
PENDING_STATES = ("Queued", "Sending", "Draft")


def _count(filters):
    return frappe.db.count("Kodafreight SMS Message", filters)


@frappe.whitelist()
def platform_dashboard():
    """Aggregates for the platform administrator."""
    if not is_platform_admin():
        frappe.throw(_("Not permitted."), frappe.PermissionError)

    clients = frappe.get_all("Kodafreight Client", fields=["name", "status"])
    totals = frappe.db.sql(
        """
        select
            count(*) as total,
            sum(case when status in ('Sent','Delivered') then 1 else 0 end) as successful,
            sum(case when status = 'Delivered' then 1 else 0 end) as delivered,
            sum(case when status = 'Failed' then 1 else 0 end) as failed,
            sum(case when status in ('Queued','Sending','Draft') then 1 else 0 end) as pending,
            coalesce(sum(credits_used), 0) as credits
        from `tabKodafreight SMS Message`
        """,
        as_dict=True,
    )[0]

    credits = frappe.db.sql(
        """select coalesce(sum(allocated),0) allocated, coalesce(sum(used),0) used,
                  coalesce(sum(balance),0) balance
           from `tabKodafreight SMS Credit Account`""",
        as_dict=True,
    )[0]

    return {
        "clients": {
            "total": len(clients),
            "active": len([c for c in clients if c.status == "Active"]),
            "inactive": len([c for c in clients if c.status == "Inactive"]),
            "suspended": len([c for c in clients if c.status == "Suspended"]),
        },
        "messages": {
            "total": cint(totals.total),
            "successful": cint(totals.successful),
            "delivered": cint(totals.delivered),
            "failed": cint(totals.failed),
            "pending": cint(totals.pending),
        },
        "credits": {
            "allocated": cint(credits.allocated),
            "used": cint(credits.used),
            "remaining": cint(credits.balance),
        },
        "sender_names": {
            "total": frappe.db.count("Kodafreight Sender Name"),
            "active": frappe.db.count("Kodafreight Sender Name", {"status": "Active"}),
        },
        "sms_numbers": {
            "total": frappe.db.count("Kodafreight SMS Number"),
            "assigned": frappe.db.count("Kodafreight SMS Number", {"status": "Assigned"}),
        },
        "trend": usage_trend(days=14, client=None),
        "top_clients": frappe.db.sql(
            """select client, count(*) as messages
               from `tabKodafreight SMS Message`
               where creation >= %s group by client order by messages desc limit 5""",
            (add_days(today(), -30),), as_dict=True,
        ),
        "recent_activity": frappe.get_all(
            "Kodafreight Audit Log",
            fields=["action", "client", "user", "status", "creation"],
            order_by="creation desc", limit_page_length=10,
        ),
    }


@frappe.whitelist()
def client_dashboard(client=None):
    """Everything the client dashboard needs, in one round trip."""
    client = resolve_client(client)
    doc = frappe.get_cached_doc("Kodafreight Client", client)

    base = {"client": client}
    stats = frappe.db.sql(
        """
        select
            count(*) as total,
            sum(case when status in ('Sent','Delivered') then 1 else 0 end) as successful,
            sum(case when status = 'Delivered' then 1 else 0 end) as delivered,
            sum(case when status = 'Failed' then 1 else 0 end) as failed,
            sum(case when status in ('Queued','Sending','Draft') then 1 else 0 end) as pending,
            sum(case when date(creation) = %(today)s then 1 else 0 end) as today_count
        from `tabKodafreight SMS Message` where client = %(client)s
        """,
        {"client": client, "today": today()},
        as_dict=True,
    )[0]

    summary = credit_service.get_summary(client)

    return {
        "client": {
            "name": doc.name,
            "client_name": doc.client_name,
            "status": doc.status,
            "email": doc.email,
            "phone": doc.phone,
            "city": doc.city,
            "country": doc.country,
            "logo": doc.logo,
            "default_sender_name": doc.default_sender_name,
        },
        "credits": {
            "allocated": cint(summary.get("allocated")),
            "used": cint(summary.get("used")),
            "remaining": cint(summary.get("balance")),
            "low_balance_threshold": cint(summary.get("low_balance_threshold")),
        },
        "messages": {
            "total": cint(stats.total),
            "successful": cint(stats.successful),
            "delivered": cint(stats.delivered),
            "failed": cint(stats.failed),
            "pending": cint(stats.pending),
            "today": cint(stats.today_count),
        },
        "senders": [
            {"sender_name": r.sender_name, "is_default": bool(r.is_default)}
            for r in doc.senders or [] if r.enabled
        ],
        "numbers": frappe.get_all(
            "Kodafreight SMS Number", filters={"client": client},
            fields=["number", "number_type", "status"]),
        "recent_messages": frappe.get_all(
            "Kodafreight SMS Message", filters={"client": client},
            fields=["name", "recipient", "sender_name", "status", "message", "parts",
                    "creation"],
            order_by="creation desc", limit_page_length=8),
        "campaigns": frappe.get_all(
            "Kodafreight SMS Campaign", filters={"client": client},
            fields=["name", "title", "status", "total_recipients", "sent_count",
                    "failed_count", "creation"],
            order_by="creation desc", limit_page_length=5),
        "trend": usage_trend(days=7, client=client),
        "contacts": frappe.db.count("Kodafreight Contact", {"client": client}),
        "categories": frappe.db.count("Kodafreight Contact Category", {"client": client}),
        **base,
    }


@frappe.whitelist()
def usage_trend(days=7, client=None):
    """Daily sent/failed counts for the chart."""
    days = cint(days) or 7
    if client:
        client = resolve_client(client)
    elif not is_platform_admin():
        client = get_user_client(throw=True)

    conditions = "creation >= %(start)s"
    params = {"start": add_days(today(), -(days - 1))}
    if client:
        conditions += " and client = %(client)s"
        params["client"] = client

    rows = frappe.db.sql(
        f"""
        select date(creation) as day,
               sum(case when status in ('Sent','Delivered') then 1 else 0 end) as sent,
               sum(case when status = 'Failed' then 1 else 0 end) as failed
        from `tabKodafreight SMS Message`
        where {conditions}
        group by date(creation) order by day
        """,
        params, as_dict=True,
    )
    by_day = {str(r.day): r for r in rows}
    series = []
    for offset in range(days - 1, -1, -1):
        day = str(getdate(add_days(today(), -offset)))
        row = by_day.get(day)
        series.append({
            "day": day,
            "sent": cint(row.sent) if row else 0,
            "failed": cint(row.failed) if row else 0,
        })
    return series
