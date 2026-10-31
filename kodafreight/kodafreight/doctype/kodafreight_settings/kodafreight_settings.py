# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document


class KodafreightSettings(Document):
    def validate(self):
        if self.default_country_code and not str(self.default_country_code).isdigit():
            frappe.throw(_("The country code must contain digits only (e.g. 255)."))
        if int(self.queue_batch_size or 0) <= 0:
            self.queue_batch_size = 20
        if int(self.request_timeout or 0) <= 0:
            self.request_timeout = 30
        if int(self.max_retries or 0) < 0:
            self.max_retries = 0

    def on_update(self):
        frappe.clear_cache(doctype="Kodafreight Settings")
