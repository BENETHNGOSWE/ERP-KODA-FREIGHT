# Copyright (c) 2026, Kodafreight and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today

from kodafreight.services import credits as credit_service
from kodafreight.utils import audit


class KodafreightClient(Document):
    def validate(self):
        self.validate_senders()
        self.validate_status()

    def validate_senders(self):
        """One default sender per client (replaces Laravel `clientactivenames`)."""
        seen = set()
        defaults = []
        for row in self.senders or []:
            if row.sender_name in seen:
                frappe.throw(_("Sender name {0} is assigned more than once.").format(row.sender_name))
            seen.add(row.sender_name)
            if not row.assigned_on:
                row.assigned_on = today()
            if row.is_default:
                defaults.append(row)

        if len(defaults) > 1:
            frappe.throw(_("Only one sender name can be marked as the default/active name."))

        if not defaults and self.senders:
            enabled = [r for r in self.senders if r.enabled]
            if enabled:
                enabled[0].is_default = 1
                defaults = [enabled[0]]

        self.default_sender_name = defaults[0].sender_name if defaults else None

    def validate_status(self):
        if self.status == "Suspended" and not self.suspended_reason:
            frappe.throw(_("A suspension reason is required when suspending a client."))

    def after_insert(self):
        credit_service.get_account(self.name)
        audit.log("client.created", client=self.name, reference_doctype=self.doctype,
                  reference_name=self.name, details={"client_name": self.client_name})

    def on_update(self):
        if self.has_value_changed("status"):
            audit.log("client.status_changed", client=self.name,
                      reference_doctype=self.doctype, reference_name=self.name,
                      details={"status": self.status})
            if self.status != "Active":
                # Stop anything still queued for a disabled tenant.
                frappe.db.sql(
                    """UPDATE `tabKodafreight SMS Message`
                       SET status = 'Cancelled',
                           error_message = 'Client is no longer active'
                       WHERE client = %s AND status IN ('Queued', 'Draft')""",
                    (self.name,),
                )

    def on_trash(self):
        for doctype in (
            "Kodafreight SMS Message", "Kodafreight SMS Campaign", "Kodafreight Contact",
            "Kodafreight Contact Category", "Kodafreight Client User",
            "Kodafreight SMS Credit Transaction", "Kodafreight SMS Credit Account",
        ):
            if frappe.db.exists(doctype, {"client": self.name}):
                frappe.throw(
                    _("Cannot delete {0}: {1} records still reference this client. "
                      "Set the status to Inactive instead.").format(self.name, doctype)
                )

    # -- API -------------------------------------------------------------------
    @frappe.whitelist()
    def allocate_credits(self, quantity, remarks=None):
        frappe.only_for("Kodafreight Platform Admin")
        txn = credit_service.allocate(self.name, int(quantity), remarks=remarks,
                                      reference_doctype=self.doctype, reference_name=self.name)
        audit.log("credits.allocated", client=self.name, reference_doctype=self.doctype,
                  reference_name=self.name, details={"quantity": int(quantity)})
        self.reload()
        return {"transaction": txn.name, "balance": txn.balance_after}
