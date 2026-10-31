"""Client-owned branch, based on the Laravel Branch model."""

import frappe
from frappe import _
from frappe.model.document import Document

from kodafreight.utils.tenant import get_user_client, is_platform_admin


class KodafreightBranch(Document):
    def validate(self):
        user = frappe.session.user
        own = get_user_client(user)
        if own:
            if self.client and self.client != own:
                frappe.throw(_("You cannot manage a branch for another client."), frappe.PermissionError)
            self.client = own
        elif not is_platform_admin(user):
            frappe.throw(_("Your user is not linked to a Kodafreight client."), frappe.PermissionError)
        if not self.client or not frappe.db.exists("Kodafreight Client", self.client):
            frappe.throw(_("A valid client is required."))
        # Never allow an existing branch to be moved across tenants.
        if not self.is_new():
            previous = frappe.db.get_value(self.doctype, self.name, "client")
            if previous and previous != self.client:
                frappe.throw(_("A branch cannot be moved to another client."), frappe.PermissionError)
