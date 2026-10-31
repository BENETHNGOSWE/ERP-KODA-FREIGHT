# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document


class KodafreightSMSProvider(Document):
    def validate(self):
        if not self.api_base_url.startswith(("http://", "https://")):
            frappe.throw(_("The API base URL must start with http:// or https://"))
        if self.api_base_url.startswith("http://") and self.verify_ssl:
            frappe.msgprint(_("The base URL is not using TLS; credentials will travel in clear."))
        if self.is_default:
            frappe.db.set_value(
                "Kodafreight SMS Provider", {"name": ("!=", self.name)}, "is_default", 0,
                update_modified=False,
            )

    @frappe.whitelist()
    def test_connection(self, recipient=None, message=None, sender_name=None):
        """Send a single test SMS. Platform admins only; never touches client credits."""
        frappe.only_for("Kodafreight Platform Admin")
        from kodafreight.services.sms.factory import build_driver, provider_settings

        driver = build_driver(provider_settings(self))
        result = driver.send(
            [recipient or "255700000000"],
            message or "Kodafreight connectivity test",
            sender_name or "KODASMS",
            reference="KF_TEST",
        )
        from kodafreight.utils import audit
        audit.log("provider.test", reference_doctype=self.doctype, reference_name=self.name,
                  status="Success" if result.success else "Failure",
                  details={"message": result.message})
        return {"success": result.success, "message": result.message,
                "balance": result.balance, "raw": result.raw}
