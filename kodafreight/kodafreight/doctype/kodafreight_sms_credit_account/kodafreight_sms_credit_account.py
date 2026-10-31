# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document


class KodafreightSMSCreditAccount(Document):
    def validate(self):
        self.balance = int(self.allocated or 0) - int(self.used or 0)
        if self.balance < 0 and not self.allow_negative:
            frappe.throw(_("Balance cannot be negative for this account."))

    @frappe.whitelist()
    def rebuild(self):
        frappe.only_for("Kodafreight Platform Admin")
        from kodafreight.services import credits as credit_service

        result = credit_service.rebuild_account(self.client)
        self.reload()
        return result
