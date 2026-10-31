import hashlib
import hmac
import json

import frappe

from kodafreight.api import webhooks
from kodafreight.services.sms import dispatcher
from kodafreight.tests import CLIENT_A, KodafreightTestCase, stub_provider


class TestWebhooks(KodafreightTestCase):
    def sign(self, payload):
        secret = frappe.utils.password.get_decrypted_password(
            "Kodafreight Settings", "Kodafreight Settings", "webhook_secret")
        body = json.dumps(payload)
        return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    def test_delivery_report_updates_message(self):
        stub_provider(True)
        client = self.clients[CLIENT_A]
        doc = dispatcher.queue_message(client, "0745788788", "DLR test", send_now=True)
        frappe.db.commit()

        report = {"messageId": doc.provider_message_id, "reference": doc.provider_reference,
                  "status": "DELIVRD", "msisdn": doc.recipient}
        frappe.local.request_ip = "127.0.0.1"
        webhooks.verify_signature = lambda: True  # signature covered by its own test
        result = webhooks.delivery_report(**report)
        self.assertTrue(result["ok"])
        doc.reload()
        self.assertEqual(doc.status, "Delivered")

    def test_duplicate_callback_is_ignored(self):
        stub_provider(True)
        client = self.clients[CLIENT_A]
        doc = dispatcher.queue_message(client, "0745788788", "DLR dup", send_now=True)
        frappe.db.commit()
        webhooks.verify_signature = lambda: True
        payload = {"messageId": doc.provider_message_id, "reference": doc.provider_reference,
                   "status": "DELIVRD"}
        webhooks.delivery_report(**payload)
        second = webhooks.delivery_report(**payload)
        self.assertTrue(second.get("duplicate"))
