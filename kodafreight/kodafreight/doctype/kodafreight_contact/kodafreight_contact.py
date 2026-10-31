# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document

from kodafreight.services.sms import phone as phone_service
from kodafreight.utils.tenant import get_user_client, is_platform_admin


class KodafreightContact(Document):
    def validate(self):
        own = get_user_client()
        if own:
            if self.client and self.client != own:
                frappe.throw(_("You cannot create contacts for another client."),
                             frappe.PermissionError)
            self.client = own
        elif not self.client and not is_platform_admin():
            frappe.throw(_("Client is required."))

        try:
            self.msisdn = phone_service.normalise(
                self.phone,
                country_code=frappe.db.get_single_value(
                    "Kodafreight Settings", "default_country_code") or "255",
                national_length=frappe.db.get_single_value(
                    "Kodafreight Settings", "national_number_length") or 9,
            )
        except phone_service.InvalidPhoneNumber as exc:
            frappe.throw(_(str(exc)))

        if self.category:
            category_client = frappe.db.get_value(
                "Kodafreight Contact Category", self.category, "client")
            if category_client != self.client:
                frappe.throw(_("The selected category belongs to another client."))

        duplicate = frappe.db.get_value(
            "Kodafreight Contact",
            {"client": self.client, "msisdn": self.msisdn, "name": ("!=", self.name or "")},
            "name",
        )
        if duplicate:
            frappe.throw(_("Contact {0} already uses the number {1} for this client.")
                         .format(duplicate, self.msisdn))

        if not self.assigned_to:
            self.assigned_to = frappe.session.user
