# Copyright (c) 2026, Kodafreight and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

from kodafreight.services import credits as credit_service
from kodafreight.services.sms import dispatcher
from kodafreight.services.sms import encoding as encoding_service
from kodafreight.services.sms import phone as phone_service
from kodafreight.utils import audit
from kodafreight.utils.tenant import (
    assert_sender_allowed,
    ensure_client_active,
    get_user_client,
    is_platform_admin,
)

PLACEHOLDERS = ("{first_name}", "{last_name}", "{phone}", "{{ customer_firstname }}")


class KodafreightSMSCampaign(Document):
    """Bulk SMS.

    Replaces the Laravel loop that made one blocking HTTP call per recipient inside the web
    request. Recipients are materialised into a child table, then dispatched by background
    workers, so a 10 000 recipient blast does not hold a web worker hostage.
    """

    def validate(self):
        self.enforce_tenant()
        assert_sender_allowed(self.client, self.sender_name)
        info = encoding_service.analyse(self.message or "")
        self.encoding = info["encoding"]
        self.parts = info["parts"] or 1
        if self.status == "Draft":
            self.populate_recipients()
        self.total_recipients = len(self.recipients or [])
        self.estimated_credits = self.total_recipients * (self.parts or 1)
        self.update_counts()

    def enforce_tenant(self):
        own = get_user_client()
        if own:
            if self.client and self.client != own:
                frappe.throw(_("You cannot create campaigns for another client."),
                             frappe.PermissionError)
            self.client = own
        elif not self.client and not is_platform_admin():
            frappe.throw(_("Client is required."))
        ensure_client_active(self.client)

    def populate_recipients(self):
        """Resolve the recipient source into concrete rows (idempotent for manual lists)."""
        if self.recipient_source == "Manual":
            self.normalise_manual_rows()
            return

        filters = {"client": self.client, "enabled": 1}
        if self.recipient_source == "By Category":
            if not self.category:
                frappe.throw(_("Select a category."))
            filters["category"] = self.category
        elif self.recipient_source == "My Contacts":
            # Laravel parity: non-admin staff may only message their own contacts.
            filters["assigned_to"] = frappe.session.user

        contacts = frappe.get_all(
            "Kodafreight Contact",
            filters=filters,
            fields=["name", "first_name", "last_name", "msisdn"],
            limit_page_length=0,
        )
        self.set("recipients", [])
        for contact in contacts:
            if not contact.msisdn:
                continue
            self.append("recipients", {
                "contact": contact.name,
                "recipient_name": " ".join(filter(None, [contact.first_name, contact.last_name])),
                "phone": contact.msisdn,
                "status": "Pending",
            })

    def normalise_manual_rows(self):
        country = frappe.db.get_single_value("Kodafreight Settings", "default_country_code")
        length = frappe.db.get_single_value("Kodafreight Settings", "national_number_length")
        seen = set()
        cleaned = []
        for row in self.recipients or []:
            msisdn = phone_service.try_normalise(row.phone, country or "255", cint(length) or 9)
            if not msisdn:
                frappe.throw(_("Row {0}: '{1}' is not a valid phone number.")
                             .format(row.idx, row.phone))
            if msisdn in seen:
                continue  # de-duplicate: the Laravel version sent twice
            seen.add(msisdn)
            row.phone = msisdn
            cleaned.append(row)
        self.set("recipients", cleaned)

    def update_counts(self):
        rows = self.recipients or []
        self.sent_count = len([r for r in rows if r.status in ("Sent", "Delivered")])
        self.delivered_count = len([r for r in rows if r.status == "Delivered"])
        self.failed_count = len([r for r in rows if r.status == "Failed"])

    # -- actions ---------------------------------------------------------------
    @frappe.whitelist()
    def queue_campaign(self):
        frappe.has_permission(self.doctype, "write", self, throw=True)
        if self.status not in ("Draft", "Partially Failed"):
            frappe.throw(_("Only draft or partially failed campaigns can be queued."))
        if not self.recipients:
            frappe.throw(_("This campaign has no recipients."))

        required = len(self.recipients) * (self.parts or 1)
        if not credit_service.has_credits(self.client, required):
            frappe.throw(_("Insufficient SMS credits: {0} credits are required, {1} available.")
                         .format(required, credit_service.get_balance(self.client)))

        self.db_set("status", "Queued")
        frappe.enqueue(
            "kodafreight.kodafreight.doctype.kodafreight_sms_campaign."
            "kodafreight_sms_campaign.process_campaign",
            queue="long",
            timeout=3600,
            campaign=self.name,
            enqueue_after_commit=True,
        )
        audit.log("campaign.queued", client=self.client, reference_doctype=self.doctype,
                  reference_name=self.name, details={"recipients": len(self.recipients),
                                                     "credits": required})
        return {"status": "Queued", "recipients": len(self.recipients)}

    @frappe.whitelist()
    def cancel_campaign(self):
        frappe.has_permission(self.doctype, "write", self, throw=True)
        if self.status in ("Completed",):
            frappe.throw(_("A completed campaign cannot be cancelled."))
        self.db_set("status", "Cancelled")
        frappe.db.sql(
            """UPDATE `tabKodafreight SMS Message`
               SET status = 'Cancelled'
               WHERE campaign = %s AND status = 'Queued'""",
            (self.name,),
        )
        return {"status": "Cancelled"}


def render(template, row):
    """Personalise the text (Laravel used {{ customer_firstname }} + strip_tags)."""
    import re

    first = (row.get("recipient_name") or "").split(" ")[0]
    text = template or ""
    text = text.replace("{{ customer_firstname }}", first)
    text = text.replace("{first_name}", first)
    text = text.replace("{last_name}", " ".join((row.get("recipient_name") or "").split(" ")[1:]))
    text = text.replace("{phone}", row.get("phone") or "")
    return re.sub(r"<[^>]+>", "", text).strip()


def process_campaign(campaign):
    """Background worker: create + dispatch one message per recipient."""
    doc = frappe.get_doc("Kodafreight SMS Campaign", campaign)
    if doc.status not in ("Queued", "Processing"):
        return
    doc.db_set("status", "Processing")

    sent = failed = 0
    for row in doc.recipients:
        if row.status in ("Sent", "Delivered"):
            continue
        try:
            text = render(doc.message, {"recipient_name": row.recipient_name, "phone": row.phone})
            message = dispatcher.queue_message(
                client=doc.client,
                recipient=row.phone,
                message=text,
                sender_name=doc.sender_name,
                campaign=doc.name,
                contact=row.contact,
                send_now=True,
            )
            row.db_set("message", message.name)
            row.db_set("status", "Sent" if message.status in ("Sent", "Delivered") else "Failed")
            row.db_set("error", message.error_message)
            if message.status in ("Sent", "Delivered"):
                sent += 1
            else:
                failed += 1
        except Exception as exc:
            row.db_set("status", "Failed")
            row.db_set("error", str(exc)[:200])
            failed += 1
            frappe.log_error(frappe.get_traceback(), f"Kodafreight campaign {campaign}")
        frappe.db.commit()

    status = "Completed" if not failed else ("Partially Failed" if sent else "Partially Failed")
    doc.db_set({"status": status, "sent_count": sent, "failed_count": failed})
    frappe.db.commit()
    audit.log("campaign.processed", client=doc.client, reference_doctype=doc.doctype,
              reference_name=doc.name,
              status="Success" if not failed else "Failure",
              details={"sent": sent, "failed": failed, "finished_at": str(now_datetime())})
    return {"sent": sent, "failed": failed}
