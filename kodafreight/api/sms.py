"""Public API for SMS operations.

All endpoints are permission checked server side and always resolve the acting client from
the session (a payload cannot select another tenant). Rate limiting uses the value from
`Kodafreight Settings`.
"""

import frappe
from frappe import _
from frappe.utils import cint

from kodafreight.services import credits as credit_service
from kodafreight.services.sms import dispatcher
from kodafreight.services.sms import encoding as encoding_service
from kodafreight.services.sms import phone as phone_service
from kodafreight.utils import audit
from kodafreight.utils.tenant import (
    assert_sender_allowed,
    ensure_client_active,
    get_client_senders,
    get_default_sender,
    resolve_client,
)

MAX_BULK_RECIPIENTS = 5000


def _rate_limit(key, limit=None):
    limit = limit or cint(
        frappe.db.get_single_value("Kodafreight Settings", "api_rate_limit_per_minute")
    ) or 60
    cache_key = f"kodafreight:rl:{key}:{frappe.session.user}"
    count = cint(frappe.cache().get_value(cache_key) or 0) + 1
    frappe.cache().set_value(cache_key, count, expires_in_sec=60)
    if count > limit:
        frappe.throw(_("Rate limit exceeded. Try again in a minute."), frappe.RateLimitExceededError)


@frappe.whitelist()
def send_sms(recipient, message, sender_name=None, client=None, send_now=1):
    """Send a single SMS. Returns {message, status, credits_used, balance}."""
    _rate_limit("send_sms")
    client = resolve_client(client)
    ensure_client_active(client)

    if not (message or "").strip():
        frappe.throw(_("Message text is required."))

    sender_name = sender_name or get_default_sender(client)
    if not sender_name:
        frappe.throw(_("No sender name is assigned to your account."))
    assert_sender_allowed(client, sender_name)

    doc = dispatcher.queue_message(
        client=client,
        recipient=recipient,
        message=message,
        sender_name=sender_name,
        send_now=cint(send_now),
    )
    return {
        "message": doc.name,
        "status": doc.status,
        "recipient": doc.recipient,
        "parts": doc.parts,
        "credits_used": doc.credits_used,
        "error": doc.error_message,
        "balance": credit_service.get_balance(client),
    }


@frappe.whitelist()
def send_bulk_sms(recipients, message, sender_name=None, client=None, title=None):
    """Create a campaign from a list of numbers and queue it in the background."""
    _rate_limit("send_bulk_sms", limit=10)
    client = resolve_client(client)
    ensure_client_active(client)

    if isinstance(recipients, str):
        recipients = [r for r in recipients.replace("\n", ",").split(",") if r.strip()]
    if not recipients:
        frappe.throw(_("At least one recipient is required."))
    if len(recipients) > MAX_BULK_RECIPIENTS:
        frappe.throw(_("A single campaign is limited to {0} recipients.")
                     .format(MAX_BULK_RECIPIENTS))
    if not (message or "").strip():
        frappe.throw(_("Message text is required."))

    sender_name = sender_name or get_default_sender(client)
    assert_sender_allowed(client, sender_name)

    campaign = frappe.get_doc({
        "doctype": "Kodafreight SMS Campaign",
        "title": title or _("Bulk SMS"),
        "client": client,
        "sender_name": sender_name,
        "message": message,
        "recipient_source": "Manual",
        "status": "Draft",
        "recipients": [{"phone": str(r).strip(), "status": "Pending"} for r in recipients],
    })
    campaign.insert()
    result = campaign.queue_campaign()
    return {"campaign": campaign.name, **result}


@frappe.whitelist()
def preview_message(message):
    """Character / part / credit preview used by the send form."""
    info = encoding_service.analyse(message or "")
    return {
        "characters": info["characters"],
        "length": info["length"],
        "parts": info["parts"],
        "encoding": info["encoding"],
        "per_part": info["per_part"],
    }


@frappe.whitelist()
def validate_number(number):
    country = frappe.db.get_single_value("Kodafreight Settings", "default_country_code") or "255"
    length = cint(frappe.db.get_single_value("Kodafreight Settings", "national_number_length")) or 9
    msisdn = phone_service.try_normalise(number, country, length)
    return {"valid": bool(msisdn), "msisdn": msisdn}


@frappe.whitelist()
def my_senders(client=None):
    client = resolve_client(client)
    return get_client_senders(client)


@frappe.whitelist()
def my_balance(client=None):
    client = resolve_client(client)
    return credit_service.get_summary(client)


@frappe.whitelist()
def message_history(client=None, status=None, limit=20, start=0, search=None):
    client = resolve_client(client)
    filters = {"client": client}
    if status:
        filters["status"] = status
    if search:
        filters["recipient"] = ("like", f"%{search}%")
    return frappe.get_all(
        "Kodafreight SMS Message",
        filters=filters,
        fields=["name", "recipient", "sender_name", "status", "parts", "credits_used",
                "message", "creation", "sent_at", "delivered_at", "error_message"],
        order_by="creation desc",
        start=cint(start),
        limit_page_length=cint(limit),
    )


@frappe.whitelist()
def retry_failed(client=None, limit=100):
    """Re-queue failed messages of the acting client."""
    client = resolve_client(client)
    names = frappe.get_all(
        "Kodafreight SMS Message",
        filters={"client": client, "status": "Failed"},
        pluck="name",
        limit_page_length=cint(limit),
    )
    for name in names:
        frappe.db.set_value("Kodafreight SMS Message", name,
                            {"status": "Queued", "error_message": None, "retry_count": 0},
                            update_modified=False)
    audit.log("sms.retry_failed", client=client, details={"count": len(names)})
    return {"requeued": len(names)}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def sender_name_query(doctype, txt, searchfield, start, page_len, filters):
    """Link query: only sender names assigned to the given (or the acting) client."""
    client = resolve_client((filters or {}).get("client"))
    return frappe.db.sql(
        """
        select cs.sender_name
        from `tabKodafreight Client Sender` cs
        inner join `tabKodafreight Sender Name` sn on sn.name = cs.sender_name
        where cs.parent = %(client)s and cs.enabled = 1 and sn.status = 'Active'
          and cs.sender_name like %(txt)s
        order by cs.is_default desc, cs.sender_name asc
        limit %(start)s, %(page_len)s
        """,
        {"client": client, "txt": f"%{txt or ''}%", "start": cint(start),
         "page_len": cint(page_len) or 20},
    )
