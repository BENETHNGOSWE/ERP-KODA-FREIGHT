# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document


class KodafreightSenderName(Document):
    def validate(self):
        self.sender_name = (self.sender_name or "").strip()
        if not self.sender_name:
            frappe.throw(_("Sender name is required."))
        if len(self.sender_name) > 11:
            frappe.throw(_("A sender name cannot exceed 11 characters (GSM specification)."))
        if not self.sender_name.replace(" ", "").replace("-", "").replace("_", "").isalnum():
            frappe.throw(_("A sender name may only contain letters, digits, spaces, - and _."))

    def on_update(self):
        if self.has_value_changed("status") and self.status == "Inactive":
            frappe.msgprint(
                _("Sender name {0} is now inactive. Clients can no longer send with it.")
                .format(self.name)
            )

    @frappe.whitelist()
    def assign_to_client(self, client, is_default=0):
        """Assign this sender to a client (Laravel `AssignnameController@store`)."""
        frappe.only_for("Kodafreight Platform Admin")
        doc = frappe.get_doc("Kodafreight Client", client)
        for row in doc.senders or []:
            if row.sender_name == self.name:
                row.enabled = 1
                if int(is_default or 0):
                    for other in doc.senders:
                        other.is_default = 0
                    row.is_default = 1
                doc.save()
                return {"assigned": True, "already": True}
        if int(is_default or 0):
            for other in doc.senders or []:
                other.is_default = 0
        doc.append("senders", {
            "sender_name": self.name,
            "enabled": 1,
            "is_default": int(is_default or 0),
            "assigned_on": frappe.utils.today(),
        })
        doc.save()
        from kodafreight.utils import audit
        audit.log("sender.assigned", client=client, reference_doctype=self.doctype,
                  reference_name=self.name)
        return {"assigned": True}
