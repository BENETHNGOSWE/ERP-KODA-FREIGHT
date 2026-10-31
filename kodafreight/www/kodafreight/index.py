"""Client portal dashboard controller (`/kodafreight`)."""

import frappe
from frappe import _
from frappe.utils import format_datetime, getdate, now_datetime

no_cache = 1

MAX_BAR_HEIGHT = 110


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw(_("Please log in to view your dashboard."), frappe.PermissionError)

    from kodafreight.api.dashboard import client_dashboard
    from kodafreight.utils.tenant import get_user_client, is_platform_admin

    client = get_user_client()
    if not client:
        if is_platform_admin():
            frappe.local.flags.redirect_location = "/app/kodafreight"
            raise frappe.Redirect
        frappe.throw(_("Your user is not linked to a Kodafreight client."),
                     frappe.PermissionError)

    data = client_dashboard(client)
    decorate(data)

    context.no_cache = 1
    context.title = _("Kodafreight Dashboard")
    context.data = data
    context.full_name = frappe.utils.get_fullname(frappe.session.user)
    context.today_label = format_datetime(now_datetime(), "EEEE, d MMMM yyyy")
    context.used_percent = percent(data["credits"]["used"], data["credits"]["allocated"])
    context.success_rate = percent(data["messages"]["successful"], data["messages"]["total"])
    return context


def decorate(data):
    """Precompute presentation values so the template stays logic free."""
    peak = max([p["sent"] + p["failed"] for p in data["trend"]] or [0]) or 1
    for point in data["trend"]:
        point["label"] = getdate(point["day"]).strftime("%a")
        point["sent_h"] = int(point["sent"] / peak * MAX_BAR_HEIGHT) or (4 if point["sent"] else 2)
        point["failed_h"] = int(point["failed"] / peak * MAX_BAR_HEIGHT) or (
            4 if point["failed"] else 2)
    for row in data["recent_messages"]:
        row["when"] = frappe.utils.pretty_date(row["creation"])
        text = row.get("message") or ""
        row["message"] = text if len(text) <= 70 else text[:67] + "..."
    return data


def percent(part, total):
    try:
        if not total:
            return 0
        return round(float(part) / float(total) * 100, 1)
    except Exception:
        return 0
