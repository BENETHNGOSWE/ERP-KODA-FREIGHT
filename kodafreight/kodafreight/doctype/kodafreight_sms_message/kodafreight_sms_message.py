# Copyright (c) 2026, Kodafreight and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from kodafreight.services.sms import dispatcher
from kodafreight.utils.tenant import (
    assert_sender_allowed,
    ensure_client_active,
    get_default_sender,
    get_user_client,
    is_platform_admin,
)


class KodafreightSMSMessage(Document):
    def validate(self):
        self.enforce_tenant()
        if self.direction == "Inbound":
            # Inbound (MO) messages have no sender name of ours and cost no credits.
            self.credits_used = 0
            self.parts = 1
            return
        if not self.sender_name:
            self.sender_name = get_default_sender(self.client)
        if not self.sender_name:
            frappe.throw(_("No sender name is assigned to client {0}.").format(self.client))
        assert_sender_allowed(self.client, self.sender_name)
        dispatcher.prepare_message(self)
        if self.is_new() and not self.queued_at:
            self.queued_at = now_datetime()
        self.validate_immutability()

    def enforce_tenant(self):
        own = get_user_client()
        if own:
            if self.client and self.client != own:
                frappe.throw(_("You cannot create messages for another client."),
                             frappe.PermissionError)
            self.client = own
        elif not self.client and not is_platform_admin():
            frappe.throw(_("Client is required."))
        if self.client:
            ensure_client_active(self.client)

    def validate_immutability(self):
        """Content of an already dispatched message can never be edited."""
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if not before:
            return
        if before.status in ("Sent", "Delivered") and (
            self.message != before.message or self.recipient != before.recipient
            or self.sender_name != before.sender_name
        ):
            frappe.throw(_("A message that has already been sent cannot be modified."))

    def before_insert(self):
        if not self.status:
            self.status = "Queued"

    @frappe.whitelist()
    def send_now(self):
        frappe.has_permission(self.doctype, "write", self, throw=True)
        dispatcher.send_message(self.name, force=True)
        self.reload()
        return {"status": self.status, "error": self.error_message}

    @frappe.whitelist()
    def retry(self):
        frappe.has_permission(self.doctype, "write", self, throw=True)
        if self.status not in ("Failed", "Cancelled"):
            frappe.throw(_("Only failed or cancelled messages can be retried."))
        self.db_set("status", "Queued")
        self.db_set("error_message", None)
        self.db_set("next_retry_at", None)
        dispatcher.send_message(self.name, force=True)
        self.reload()
        return {"status": self.status}
