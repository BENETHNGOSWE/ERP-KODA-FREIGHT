"""Release Order - the money gate.

Laravel enforced this with `if ($invoice->status != 1)` inside the *print* route, which
meant the rule only applied if the user went through that one URL. Here it lives on a
document that must exist before any release paperwork can be printed, so it cannot be
bypassed.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from kodafreight.services import freight
from kodafreight.utils.tenant import get_user_client, is_platform_admin


class ReleaseOrder(Document):
    def validate(self):
        self._enforce_tenant()
        state = freight.assert_invoice_paid(self.sales_invoice)
        if state["customer"] != self.customer:
            frappe.throw(
                _("Invoice {0} belongs to customer {1}, not {2}.").format(
                    state["name"], state["customer"], self.customer
                )
            )
        self.invoice_grand_total = state["grand_total"]
        self.invoice_outstanding = state["outstanding_amount"]
        if not flt(self.packages_count):
            self.packages_count = len(freight.loads_for_invoice(self.sales_invoice))

    def _enforce_tenant(self):
        user = frappe.session.user
        own = get_user_client(user)
        if own:
            if self.client and self.client != own:
                frappe.throw(
                    _("You cannot release cargo for another client."), frappe.PermissionError
                )
            self.client = own
        elif not is_platform_admin(user):
            frappe.throw(
                _("Your user is not linked to a Kodafreight client."), frappe.PermissionError
            )

    def on_submit(self):
        freight.set_load_status(freight.loads_for_invoice(self.sales_invoice), "Released")

    def on_cancel(self):
        freight.set_load_status(freight.loads_for_invoice(self.sales_invoice), "Arrived at Godown")
