# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import today


class KodafreightSMSNumber(Document):
    """SMS number inventory.

    NOTE: the legacy Laravel system had no number management at all. This module is a new
    capability requested for Kodafreight; it is documented as an addition, not a migration.
    """

    def validate(self):
        from kodafreight.services.sms import phone as phone_service

        try:
            self.number = phone_service.normalise(
                self.number,
                country_code=frappe.db.get_single_value(
                    "Kodafreight Settings", "default_country_code") or "255",
            )
        except phone_service.InvalidPhoneNumber:
            # Short codes (e.g. 15200) are not MSISDNs - keep them as typed.
            self.number = (self.number or "").strip()

        if self.client and self.status == "Available":
            self.status = "Assigned"
        if not self.client and self.status == "Assigned":
            frappe.throw(_("Assign a client or change the status."))
        if self.client and not self.assigned_on:
            self.assigned_on = today()
        if not self.client:
            self.assigned_on = None
