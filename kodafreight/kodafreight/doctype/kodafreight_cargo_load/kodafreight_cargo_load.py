"""Cargo Load - the freight equivalent of Laravel `Loadpackage`.

Lifecycle:
    draft -> submit -> (auto) draft Sales Invoice -> invoice submitted / paid
          -> packed into a Container -> Released -> Delivered

Field names stay close to the Laravel ones (`load_no`, `load_type`, `trucking_number`, ...)
so existing staff habits and reports still make sense, while the money itself is left to
ERPNext (Sales Invoice + Payment Entry).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

from kodafreight.services import freight
from kodafreight.utils.tenant import get_user_client, is_platform_admin


class KodafreightCargoLoad(Document):
    # ------------------------------------------------------------------ validation
    def validate(self):
        self._enforce_tenant()
        freight.apply_tariff(self)
        freight.apply_pricing(self)
        self._validate_measurements()
        self._validate_dates()
        if not self.load_no:
            self.load_no = self.name

    def _enforce_tenant(self):
        user = frappe.session.user
        own = get_user_client(user)
        if own:
            if self.client and self.client != own:
                frappe.throw(
                    _("You cannot create a cargo load for another client."),
                    frappe.PermissionError,
                )
            self.client = own
        elif not is_platform_admin(user):
            frappe.throw(
                _("Your user is not linked to a Kodafreight client."), frappe.PermissionError
            )
        if not self.client or not frappe.db.exists("Kodafreight Client", self.client):
            frappe.throw(_("A valid client is required."))
        if not self.is_new():
            previous = frappe.db.get_value(self.doctype, self.name, "client")
            if previous and previous != self.client:
                frappe.throw(
                    _("A cargo load cannot be moved to another client."), frappe.PermissionError
                )
        customer_client = frappe.db.get_value("Customer", self.customer, "kf_client")
        if customer_client and customer_client != self.client:
            frappe.throw(
                _("Customer {0} belongs to another client.").format(self.customer),
                frappe.PermissionError,
            )

    def _validate_measurements(self):
        """No silent zero volumes.

        The Laravel system accepted blank dimensions and stored `load_volume = 0.00`,
        which then produced invoices worth 0.00. A load must now be either measurable or
        explicitly flagged as a manual (agreed) volume.
        """
        if self.use_manual_volume:
            if flt(self.manual_volume) <= 0:
                frappe.throw(_("Enter the agreed manual CBM, or untick 'Use Manual CBM'."))
        elif freight.compute_volume(self.length, self.width, self.height) <= 0:
            frappe.throw(
                _(
                    "Enter Length, Width and Height in metres, or tick 'Use Manual CBM' "
                    "and type the agreed volume."
                )
            )
        if flt(self.weight) < 0:
            frappe.throw(_("Weight cannot be negative."))

    def _validate_dates(self):
        if self.transport_date and self.delivery_date:
            if getdate(self.delivery_date) < getdate(self.transport_date):
                frappe.throw(_("Expected delivery cannot be before the transport date."))

    # ------------------------------------------------------------------ lifecycle
    def on_submit(self):
        self.db_set(
            "shipment_status",
            "In Container" if self.container else "Registered",
            update_modified=False,
        )
        invoice = freight.sync_sales_invoice(self)
        if invoice:
            self.db_set("sales_invoice", invoice.name, update_modified=False)
            self.db_set(
                "invoice_status", freight.invoice_status_label(invoice), update_modified=False
            )
        else:
            self.db_set("invoice_status", freight.INVOICE_PENDING, update_modified=False)

    def on_cancel(self):
        if self.container:
            frappe.throw(
                _("Remove this load from container {0} before cancelling it.").format(
                    self.container
                )
            )
        if self.sales_invoice:
            invoice = frappe.db.get_value(
                "Sales Invoice",
                self.sales_invoice,
                ["docstatus", "grand_total", "outstanding_amount"],
                as_dict=True,
            )
            if invoice and invoice.docstatus == 1:
                paid = flt(invoice.grand_total) - flt(invoice.outstanding_amount)
                if paid > 0:
                    frappe.throw(
                        _(
                            "Invoice {0} already has {1} of payments recorded. Reverse the "
                            "payment before cancelling this load."
                        ).format(self.sales_invoice, frappe.format(paid, {"fieldtype": "Currency"}))
                    )
        self.db_set("shipment_status", "Cancelled", update_modified=False)

    # ------------------------------------------------------------------ actions
    @frappe.whitelist()
    def make_invoice(self, submit=0):
        """Price and invoice the load (Laravel: price the invoice, then bill it)."""
        self.check_permission("write")
        freight.apply_tariff(self)
        freight.apply_pricing(self)
        if not freight.is_priced(self):
            frappe.throw(
                _("Set a CBM rate with a billable volume, or a transport fee, before invoicing.")
            )
        self.flags.ignore_permissions = True
        self.save()
        invoice = freight.sync_sales_invoice(self, submit=cint(submit))
        self.db_set("sales_invoice", invoice.name, update_modified=False)
        self.db_set(
            "invoice_status", freight.invoice_status_label(invoice), update_modified=False
        )
        return invoice.name

    @frappe.whitelist()
    def refresh_invoice_status(self):
        """Re-read the payment state of the linked invoice (Paid / Partly Paid)."""
        self.check_permission("read")
        freight.refresh_load_from_invoice(self.name)
        return frappe.db.get_value(self.doctype, self.name, "invoice_status")
