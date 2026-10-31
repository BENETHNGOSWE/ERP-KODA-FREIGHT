# Copyright (c) 2026, Kodafreight and contributors

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, getdate, random_string, today


class KodafreightSubscription(Document):
    def validate(self):
        if getdate(self.end_date) < getdate(self.start_date):
            frappe.throw(_("The end date cannot be before the start date."))
        if not self.token:
            self.token = random_string(30)
        if getdate(self.end_date) < getdate(today()) and self.status == "Active":
            self.status = "Expired"

    def before_insert(self):
        plan = frappe.get_cached_doc("Kodafreight Plan", self.plan)
        if not self.end_date:
            self.end_date = add_days(self.start_date or today(), plan.validity_days or 30)
        if not self.amount:
            self.amount = plan.yearly_fee if self.billing_cycle == "Yearly" else plan.monthly_fee

    def on_update(self):
        if self.status == "Active" and not self.credits_allocated:
            plan = frappe.get_cached_doc("Kodafreight Plan", self.plan)
            if plan.included_credits:
                from kodafreight.services import credits as credit_service

                credit_service.allocate(
                    self.client, plan.included_credits,
                    remarks=f"Subscription {self.name} ({plan.name})",
                    reference_doctype=self.doctype, reference_name=self.name,
                )
                self.db_set("credits_allocated", plan.included_credits)
