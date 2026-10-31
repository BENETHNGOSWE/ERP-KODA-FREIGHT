"""Delivery Order - cargo physically leaves the godown.

Laravel stored only driver name, driver phone and vehicle registration against an
invoice. Here the same three fields are mandatory, the invoice must be paid, and the
cargo loads inside that invoice are moved to "Delivered" so the shipment status stops
lying.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from kodafreight.services import freight
from kodafreight.utils.tenant import get_user_client, is_platform_admin


class KodafreightDeliveryOrder(Document):
    def validate(self):
        self._enforce_tenant()
        freight.assert_invoice_paid(self.sales_invoice)
        if self.release_order:
            ro = frappe.db.get_value(
                "Kodafreight Release Order",
                self.release_order,
                ["docstatus", "sales_invoice", "customer"],
                as_dict=True,
            )
            if not ro:
                frappe.throw(_("Release Order {0} does not exist.").format(self.release_order))
            if ro.docstatus != 1:
                frappe.throw(
                    _("Release Order {0} must be submitted first.").format(self.release_order)
                )
            if ro.sales_invoice != self.sales_invoice or ro.customer != self.customer:
                frappe.throw(_("The release order does not match this invoice and customer."))
        for fieldname, label in (
            ("driver_name", _("Driver Name")),
            ("driver_number", _("Driver Phone")),
            ("vehicle_reg_no", _("Vehicle Registration")),
        ):
            if not self.get(fieldname):
                frappe.throw(_("{0} is required before cargo can leave the godown.").format(label))

    def _enforce_tenant(self):
        user = frappe.session.user
        own = get_user_client(user)
        if own:
            if self.client and self.client != own:
                frappe.throw(
                    _("You cannot create a delivery order for another client."),
                    frappe.PermissionError,
                )
            self.client = own
        elif not is_platform_admin(user):
            frappe.throw(
                _("Your user is not linked to a Kodafreight client."), frappe.PermissionError
            )

    def on_submit(self):
        self.db_set("delivery_status", "Dispatched", update_modified=False)
        freight.set_load_status(freight.loads_for_invoice(self.sales_invoice), "Delivered")

    def on_cancel(self):
        self.db_set("delivery_status", "Pending", update_modified=False)
        freight.set_load_status(freight.loads_for_invoice(self.sales_invoice), "Released")
