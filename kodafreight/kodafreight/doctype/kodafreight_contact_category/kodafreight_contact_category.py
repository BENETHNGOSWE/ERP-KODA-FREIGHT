# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document

from kodafreight.utils.tenant import get_user_client, is_platform_admin


class KodafreightContactCategory(Document):
    def validate(self):
        own = get_user_client()
        if own:
            self.client = own
        elif not self.client and not is_platform_admin():
            frappe.throw(_("Client is required."))
        existing = frappe.db.get_value(
            "Kodafreight Contact Category",
            {"client": self.client, "category_name": self.category_name,
             "name": ("!=", self.name or "")},
            "name",
        )
        if existing:
            frappe.throw(_("Category {0} already exists for this client.")
                         .format(self.category_name))
