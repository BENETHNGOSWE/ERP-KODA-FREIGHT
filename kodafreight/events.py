"""Document event handlers (Laravel had no event listeners at all)."""

import frappe

from kodafreight.utils.tenant import clear_user_client_cache


def on_message_created(doc, method=None):
    """Immediate dispatch for single messages when the queue is not used."""
    if doc.status != "Queued":
        return
    if doc.campaign:
        return  # campaigns are dispatched by their own worker
    if getattr(frappe.flags, "in_test", False):
        # Never enqueue from inside a test: the suite rolls back, so the job would outlive the
        # row it refers to, and a synchronous send_now=True in the same test would send twice.
        return
    if not frappe.db.get_single_value("Kodafreight Settings", "dispatch_enabled"):
        return
    frappe.enqueue(
        "kodafreight.tasks.send_single_message",
        queue="short",
        enqueue_after_commit=True,
        message=doc.name,
    )


def on_user_update(doc, method=None):
    clear_user_client_cache(doc.name)
