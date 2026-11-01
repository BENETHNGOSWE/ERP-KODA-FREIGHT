"""Freight Tariff - default CBM rate / exchange rate / transport fee for a client.

Laravel kept these numbers only on each invoice, so every clerk retyped them and typos
went straight into customer bills. A tariff is the single place to change them.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from kodafreight.utils.tenant import get_user_client, is_platform_admin


class FreightTariff(Document):
    def validate(self):
        user = frappe.session.user
        own = get_user_client(user)
        if own:
            if self.client and self.client != own:
                frappe.throw(
                    _("You cannot manage a tariff for another client."), frappe.PermissionError
                )
            self.client = own
        elif not is_platform_admin(user):
            frappe.throw(
                _("Your user is not linked to a Kodafreight client."), frappe.PermissionError
            )
        if self.valid_from and self.valid_to and str(self.valid_from) > str(self.valid_to):
            frappe.throw(_("Valid From cannot be after Valid To."))
        if self.is_default:
            self._clear_other_defaults()

    def _clear_other_defaults(self):
        others = frappe.get_all(
            self.doctype,
            filters={"client": self.client, "is_default": 1, "name": ["!=", self.name or ""]},
            pluck="name",
        )
        for name in others:
            frappe.db.set_value(self.doctype, name, "is_default", 0, update_modified=False)
