# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class KodafreightSMSCreditTransaction(Document):
    """Immutable ledger entry.

    Rows are always created through `kodafreight.services.credits`; direct creation from the
    desk is only allowed for platform admins and is routed back through the service so the
    account totals stay consistent.
    """

    def validate(self):
        if int(self.quantity or 0) <= 0:
            frappe.throw(_("Quantity must be greater than zero."))
        if not self.posting_datetime:
            self.posting_datetime = now_datetime()

    def before_insert(self):
        if self.flags.from_ledger:
            return
        # Manual entry from the desk: replay it through the ledger service instead.
        from kodafreight.services import credits as credit_service

        frappe.only_for("Kodafreight Platform Admin")
        txn = credit_service.post(
            self.client, self.transaction_type, self.quantity,
            reference_doctype=self.reference_doctype, reference_name=self.reference_name,
            remarks=self.remarks or "Manual entry",
        )
        # The service already created the authoritative row; abort this duplicate.
        frappe.msgprint(_("Credit transaction {0} created. New balance: {1}").format(
            txn.name, txn.balance_after))
        raise frappe.ValidationError(
            _("Transaction posted as {0}. This draft was discarded to keep the ledger "
              "consistent.").format(txn.name)
        )

    def on_trash(self):
        frappe.throw(_("Credit transactions are immutable and cannot be deleted. "
                       "Post an Adjustment or Expiry entry instead."))
