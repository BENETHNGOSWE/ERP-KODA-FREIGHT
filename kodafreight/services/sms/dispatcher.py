"""SMS dispatcher - the one place where a message is actually sent.

Pipeline for every outbound message:
    1. tenant + status checks           (client active, SMS enabled)
    2. sender ownership check           (fixes the Laravel sender-spoofing hole)
    3. recipient normalisation          (MSISDN)
    4. segmentation                     (GSM7/UCS2 parts -> credits)
    5. credit reservation (deduct)      (row-locked, before the API call)
    6. provider call                    (timeout + TLS verification)
    7. status update / refund on failure / retry scheduling
    8. audit log
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from kodafreight.services import credits as credit_service
from kodafreight.services.sms import encoding as encoding_service
from kodafreight.services.sms import phone as phone_service
from kodafreight.services.sms.factory import get_driver
from kodafreight.utils import audit
from kodafreight.utils.tenant import assert_sender_allowed, ensure_client_active

TERMINAL = ("Delivered", "Cancelled")


def settings():
    return frappe.get_cached_doc("Kodafreight Settings")


def normalise_recipient(raw):
    s = settings()
    return phone_service.normalise(
        raw,
        country_code=s.default_country_code or "255",
        national_length=cint(s.national_number_length) or 9,
    )


def prepare_message(doc):
    """Fill derived fields on a `Kodafreight SMS Message` (called from its validate)."""
    if not doc.message or not doc.message.strip():
        frappe.throw(_("Message text is required."))

    doc.recipient = normalise_recipient(doc.recipient)

    info = encoding_service.analyse(doc.message)
    doc.encoding = info["encoding"]
    doc.parts = info["parts"] or 1
    doc.character_count = info["characters"]

    charge_per_part = frappe.db.get_single_value("Kodafreight Settings", "charge_per_part")
    doc.credits_used = doc.parts if charge_per_part else 1
    return doc


def send_message(message_name, force=False):
    """Dispatch a single `Kodafreight SMS Message`. Returns the updated document."""
    doc = frappe.get_doc("Kodafreight SMS Message", message_name)

    if doc.status in TERMINAL and not force:
        return doc
    if doc.status == "Sent" and not force:
        return doc

    ensure_client_active(doc.client)
    assert_sender_allowed(doc.client, doc.sender_name)

    if not frappe.db.get_single_value("Kodafreight Settings", "dispatch_enabled"):
        return doc

    required = cint(doc.credits_used) or 1
    charged = False

    try:
        # Reserve credits first so a crash mid-flight can never send for free.
        if not doc.get("credits_charged"):
            credit_service.deduct(
                doc.client, required,
                reference_doctype=doc.doctype, reference_name=doc.name,
                remarks=f"SMS to {doc.recipient}",
            )
            charged = True
    except credit_service.InsufficientCredits as exc:
        _mark_failed(doc, str(exc), retryable=False)
        audit.log("sms.insufficient_credits", client=doc.client,
                  reference_doctype=doc.doctype, reference_name=doc.name, status="Failure",
                  details={"required": required})
        return doc

    frappe.db.set_value(doc.doctype, doc.name, "status", "Sending", update_modified=False)

    driver, provider_name = get_driver(_provider_for(doc.client))
    reference = doc.provider_reference or f"KF_{doc.name}"

    result = driver.send([doc.recipient], doc.message, doc.sender_name, reference=reference)

    doc.reload()
    doc.provider = provider_name
    doc.provider_reference = reference
    doc.provider_response = result.as_json()

    if result.success:
        doc.status = "Sent"
        doc.sent_at = now_datetime()
        doc.error_message = None
        doc.provider_message_id = result.provider_message_id
        doc.db_update()
        audit.log("sms.sent", client=doc.client, reference_doctype=doc.doctype,
                  reference_name=doc.name,
                  details={"recipient": doc.recipient, "parts": doc.parts,
                           "credits": required, "provider": provider_name})
    else:
        if charged:
            credit_service.refund(
                doc.client, required,
                reference_doctype=doc.doctype, reference_name=doc.name,
                remarks="Refund: provider rejected the message",
            )
        _mark_failed(doc, result.message, retryable=result.retryable)
        audit.log("sms.failed", client=doc.client, reference_doctype=doc.doctype,
                  reference_name=doc.name, status="Failure",
                  details={"recipient": doc.recipient, "error": result.message,
                           "provider": provider_name, "retryable": result.retryable})

    return doc


def _provider_for(client):
    return frappe.db.get_value("Kodafreight Client", client, "provider")


def _mark_failed(doc, error, retryable=False):
    s = settings()
    max_retries = cint(s.max_retries)
    retry_count = cint(doc.retry_count)

    if retryable and retry_count < max_retries:
        doc.status = "Queued"
        doc.retry_count = retry_count + 1
        doc.next_retry_at = add_to_date(
            now_datetime(), minutes=cint(s.retry_backoff_minutes) or 5
        )
    else:
        doc.status = "Failed"
    doc.error_message = (error or "")[:500]
    doc.db_update()


def queue_message(client, recipient, message, sender_name=None, campaign=None, contact=None,
                  send_now=False):
    """Create a message row (status Queued) and optionally dispatch it immediately."""
    from kodafreight.utils.tenant import get_default_sender

    ensure_client_active(client)
    sender_name = sender_name or get_default_sender(client)
    if not sender_name:
        frappe.throw(_("No sender name is assigned to client {0}.").format(client))
    assert_sender_allowed(client, sender_name)

    doc = frappe.get_doc(
        {
            "doctype": "Kodafreight SMS Message",
            "client": client,
            "recipient": recipient,
            "sender_name": sender_name,
            "message": message,
            "campaign": campaign,
            "contact": contact,
            "status": "Queued",
            "queued_at": now_datetime(),
        }
    )
    doc.insert()
    if send_now:
        send_message(doc.name)
        doc.reload()
    return doc


def process_pending(limit=None):
    """Dispatch queued messages.

    Laravel parity: batches of `queue_batch_size` (20 by default) every minute.
    Fix: each row is claimed with an atomic UPDATE ... WHERE status='Queued', so two
    overlapping scheduler runs can never send the same message twice.
    """
    s = settings()
    if not s.dispatch_enabled:
        return {"processed": 0, "skipped": "dispatch disabled"}

    limit = cint(limit) or cint(s.queue_batch_size) or 20
    now = now_datetime()

    rows = frappe.get_all(
        "Kodafreight SMS Message",
        filters={"status": "Queued"},
        or_filters=[["next_retry_at", "is", "not set"], ["next_retry_at", "<=", now]],
        fields=["name"],
        order_by="creation asc",
        limit_page_length=limit,
    )

    sent = failed = 0
    for row in rows:
        if not _claim(row.name, now):
            continue  # another worker already claimed this message
        frappe.db.commit()
        try:
            doc = send_message(row.name, force=True)
            if doc.status in ("Sent", "Delivered"):
                sent += 1
            else:
                failed += 1
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.db.set_value("Kodafreight SMS Message", row.name, {
                "status": "Failed",
                "error_message": frappe.get_traceback()[-480:],
            }, update_modified=False)
            frappe.db.commit()
            failed += 1
            frappe.log_error(frappe.get_traceback(), f"Kodafreight dispatch failed: {row.name}")

    return {"processed": len(rows), "sent": sent, "failed": failed}


def _claim(name, now):
    """Atomically move a message from Queued to Sending.

    Returns True only for the worker that actually performed the transition, which is what
    prevents the duplicate sends the Laravel cron suffered from.
    """
    frappe.db.sql(
        """UPDATE `tabKodafreight SMS Message`
           SET status = 'Sending', modified = %(now)s
           WHERE name = %(name)s AND status = 'Queued'""",
        {"name": name, "now": now},
    )
    try:
        rowcount = frappe.db._cursor.rowcount
    except Exception:  # pragma: no cover - driver without rowcount
        rowcount = None
    if rowcount is None:
        return frappe.db.get_value("Kodafreight SMS Message", name, "status") == "Sending"
    return rowcount > 0


def apply_delivery_report(message_name, status, provider_message_id=None, error=None):
    """Apply a normalised delivery report to a message (idempotent)."""
    doc = frappe.get_doc("Kodafreight SMS Message", message_name)
    if status == "Delivered":
        if doc.status != "Delivered":
            doc.status = "Delivered"
            doc.delivered_at = now_datetime()
    elif status == "Failed":
        if doc.status not in ("Delivered", "Failed"):
            doc.status = "Failed"
            doc.error_message = (error or "Reported as failed by the provider")[:500]
            # A message that never reached the handset is refunded.
            credit_service.refund(
                doc.client, cint(doc.credits_used) or 1,
                reference_doctype=doc.doctype, reference_name=doc.name,
                remarks="Refund: delivery report reported failure",
            )
    elif status == "Sent" and doc.status == "Queued":
        doc.status = "Sent"
        doc.sent_at = doc.sent_at or now_datetime()

    if provider_message_id and not doc.provider_message_id:
        doc.provider_message_id = provider_message_id
    doc.db_update()
    return doc
