"""Background / scheduled jobs."""

import frappe
from frappe.utils import add_days, getdate, today

from kodafreight.services.sms import dispatcher


def process_pending_messages():
    """Every minute: dispatch a batch of queued messages (Laravel parity, batch of 20)."""
    try:
        return dispatcher.process_pending()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Kodafreight: process_pending_messages")


def send_single_message(message):
    try:
        dispatcher.send_message(message)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Kodafreight: send_single_message {message}")


def expire_subscriptions():
    rows = frappe.get_all(
        "Kodafreight Subscription",
        filters={"status": "Active", "end_date": ("<", today())},
        pluck="name",
    )
    for name in rows:
        frappe.db.set_value("Kodafreight Subscription", name, "status", "Expired")
    if rows:
        frappe.db.commit()
    return {"expired": len(rows)}


def expire_credits():
    """Zero out balances whose expiry date has passed (only when one is set)."""
    accounts = frappe.get_all(
        "Kodafreight SMS Credit Account",
        filters={"expiry_date": ("<", today()), "balance": (">", 0)},
        fields=["name", "client", "balance"],
    )
    from kodafreight.services import credits as credit_service

    for account in accounts:
        credit_service.post(account.client, "Expiry", account.balance,
                            remarks="Credits expired")
    if accounts:
        frappe.db.commit()
    return {"expired_accounts": len(accounts)}


def notify_low_balances():
    accounts = frappe.get_all(
        "Kodafreight SMS Credit Account",
        fields=["name", "client", "balance", "low_balance_threshold"],
    )
    notified = 0
    for account in accounts:
        threshold = int(account.low_balance_threshold or 0)
        if threshold and int(account.balance or 0) <= threshold:
            recipients = frappe.get_all(
                "Kodafreight Client User",
                filters={"client": account.client, "user_role": "Client Admin", "enabled": 1},
                pluck="user",
            )
            for user in recipients:
                frappe.publish_realtime(
                    "kodafreight:low_balance",
                    {"client": account.client, "balance": account.balance},
                    user=user,
                )
            notified += 1
    return {"notified": notified}


def cleanup_delivery_logs(days=90):
    cutoff = add_days(today(), -int(days))
    stale = frappe.get_all(
        "Kodafreight SMS Delivery Log",
        filters={"creation": ("<", cutoff)},
        pluck="name",
        limit_page_length=5000,
    )
    for name in stale:
        frappe.delete_doc("Kodafreight SMS Delivery Log", name, ignore_permissions=True,
                          force=True, delete_permanently=True)
    if stale:
        frappe.db.commit()
    return {"deleted": len(stale)}
