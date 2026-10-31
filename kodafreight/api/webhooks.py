"""Delivery report / callback handling.

The Laravel system had no callback endpoint at all: a message was marked `delivered` as soon
as the broker accepted it. Kodafreight exposes a guest endpoint that is protected by

  * a shared secret (`X-Kodafreight-Signature`, HMAC-SHA256 of the raw body, or the plain
    secret for providers that cannot sign),
  * an optional IP allowlist,
  * a dedupe key so a provider retrying the same report cannot double-refund credits.
"""

import hashlib
import hmac
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from kodafreight.services.sms import dispatcher
from kodafreight.services.sms.factory import get_driver


def _raw_body():
    try:
        return frappe.request.get_data(as_text=True) or ""
    except Exception:
        return json.dumps(frappe.local.form_dict or {}, sort_keys=True)


def verify_signature():
    secret = frappe.utils.password.get_decrypted_password(
        "Kodafreight Settings", "Kodafreight Settings", "webhook_secret", raise_exception=False
    )
    if not secret:
        frappe.throw(_("Webhook secret is not configured."), frappe.AuthenticationError)

    headers = getattr(frappe.request, "headers", {}) or {}
    provided = headers.get("X-Kodafreight-Signature") or headers.get(
        "x-kodafreight-signature") or ""
    if not provided:
        frappe.throw(_("Missing signature."), frappe.AuthenticationError)

    body = _raw_body()
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not (hmac.compare_digest(provided, expected) or hmac.compare_digest(provided, secret)):
        frappe.throw(_("Invalid signature."), frappe.AuthenticationError)
    return True


def verify_ip():
    allowlist = frappe.db.get_single_value("Kodafreight Settings", "webhook_ip_allowlist")
    if not allowlist:
        return True
    allowed = {ip.strip() for ip in allowlist.split(",") if ip.strip()}
    remote = getattr(frappe.local, "request_ip", None)
    if remote not in allowed:
        frappe.throw(_("Callbacks are not accepted from {0}.").format(remote),
                     frappe.PermissionError)
    return True


@frappe.whitelist(allow_guest=True, methods=["POST"])
def delivery_report(**kwargs):
    """Provider delivery report endpoint.

    URL: /api/method/kodafreight.api.webhooks.delivery_report
    """
    verify_ip()
    verify_signature()

    payload = dict(kwargs or {})
    payload.pop("cmd", None)
    if not payload:
        try:
            payload = json.loads(_raw_body() or "{}")
        except Exception:
            payload = {}

    driver, provider_name = get_driver()
    report = driver.parse_callback(payload)

    message_name = _find_message(report)
    dedupe_key = hashlib.sha256(
        f"{report.get('provider_message_id')}|{report.get('reference')}|"
        f"{report.get('raw_status')}|{message_name}".encode()
    ).hexdigest()

    if frappe.db.exists("Kodafreight SMS Delivery Log", {"dedupe_key": dedupe_key}):
        return {"ok": True, "duplicate": True, "message": message_name}

    client = frappe.db.get_value("Kodafreight SMS Message", message_name, "client") \
        if message_name else None

    log = frappe.get_doc({
        "doctype": "Kodafreight SMS Delivery Log",
        "sms_message": message_name,
        "client": client,
        "provider": provider_name,
        "reported_status": report.get("raw_status"),
        "provider_message_id": report.get("provider_message_id"),
        "received_at": now_datetime(),
        "dedupe_key": dedupe_key,
        "payload": json.dumps(payload, default=str, indent=1)[:9000],
        "remote_ip": getattr(frappe.local, "request_ip", None),
    })
    log.flags.ignore_permissions = True
    log.insert(ignore_permissions=True)

    if message_name and report.get("status"):
        dispatcher.apply_delivery_report(
            message_name,
            report["status"],
            provider_message_id=report.get("provider_message_id"),
            error=report.get("error"),
        )

    frappe.db.commit()
    return {"ok": True, "message": message_name, "status": report.get("status")}


def _find_message(report):
    reference = report.get("reference") or ""
    if reference.startswith("KF_"):
        candidate = reference[3:].split("_")[0]
        if frappe.db.exists("Kodafreight SMS Message", candidate):
            return candidate
    if report.get("provider_message_id"):
        found = frappe.db.get_value(
            "Kodafreight SMS Message",
            {"provider_message_id": report["provider_message_id"]}, "name")
        if found:
            return found
    if reference:
        found = frappe.db.get_value(
            "Kodafreight SMS Message", {"provider_reference": reference}, "name")
        if found:
            return found
    if report.get("recipient"):
        return frappe.db.get_value(
            "Kodafreight SMS Message",
            {"recipient": report["recipient"], "status": ("in", ["Sent", "Sending"])},
            "name", order_by="creation desc")
    return None


@frappe.whitelist(allow_guest=True, methods=["POST"])
def inbound_message(**kwargs):
    """Inbound (MO) message endpoint - stores the message against the owning SMS number."""
    verify_ip()
    verify_signature()

    payload = dict(kwargs or {})
    payload.pop("cmd", None)
    to_number = payload.get("to") or payload.get("shortcode") or payload.get("destination")
    from_number = payload.get("from") or payload.get("msisdn") or payload.get("source")
    text = payload.get("text") or payload.get("message") or ""

    client = frappe.db.get_value("Kodafreight SMS Number", {"number": to_number}, "client")
    if not client:
        return {"ok": False, "error": "Unknown destination number"}

    doc = frappe.get_doc({
        "doctype": "Kodafreight SMS Message",
        "client": client,
        "recipient": from_number,
        "sender_name": frappe.db.get_value("Kodafreight Client", client, "default_sender_name"),
        "message": text,
        "direction": "Inbound",
        "status": "Delivered",
        "delivered_at": now_datetime(),
    })
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "message": doc.name}
