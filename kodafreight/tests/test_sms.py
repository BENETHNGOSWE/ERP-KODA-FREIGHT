import frappe

from kodafreight.services import credits as credit_service
from kodafreight.services.sms import dispatcher
from kodafreight.tests import CLIENT_A, CLIENT_B, KodafreightTestCase, stub_provider


class TestSMS(KodafreightTestCase):
    def test_send_success_deducts_credits(self):
        calls = []
        stub_provider(True, calls)
        client = self.clients[CLIENT_A]
        before = credit_service.get_balance(client)
        doc = dispatcher.queue_message(client, "0745788788", "Hello", send_now=True)
        doc.reload()
        self.assertEqual(doc.status, "Sent")
        self.assertEqual(doc.recipient, "255745788788")
        self.assertEqual(credit_service.get_balance(client), before - 1)
        self.assertEqual(calls[0]["payload"]["senderId"], "TESTA")

    def test_failure_refunds_credits(self):
        stub_provider(False)
        client = self.clients[CLIENT_A]
        before = credit_service.get_balance(client)
        doc = dispatcher.queue_message(client, "0745788788", "Hello", send_now=True)
        doc.reload()
        self.assertEqual(doc.status, "Failed")
        self.assertEqual(credit_service.get_balance(client), before)

    def test_multipart_message_costs_more(self):
        stub_provider(True)
        client = self.clients[CLIENT_A]
        before = credit_service.get_balance(client)
        doc = dispatcher.queue_message(client, "0745788788", "x" * 200, send_now=True)
        doc.reload()
        self.assertEqual(doc.parts, 2)
        self.assertEqual(credit_service.get_balance(client), before - 2)

    def test_invalid_number_rejected(self):
        client = self.clients[CLIENT_A]
        with self.assertRaises(Exception):
            dispatcher.queue_message(client, "12", "Hello")

    def test_empty_message_rejected(self):
        client = self.clients[CLIENT_A]
        with self.assertRaises(frappe.ValidationError):
            dispatcher.queue_message(client, "0745788788", "   ")

    def test_cross_client_sender_blocked(self):
        client = self.clients[CLIENT_A]
        with self.assertRaises(frappe.PermissionError):
            dispatcher.queue_message(client, "0745788788", "Hi", sender_name="TESTB")

    def test_queue_is_processed_once(self):
        calls = []
        stub_provider(True, calls)
        client = self.clients[CLIENT_A]
        for i in range(3):
            dispatcher.queue_message(client, f"07457887{i:02d}", "Batch")
        dispatcher.process_pending()
        first = len(calls)
        dispatcher.process_pending()
        self.assertEqual(len(calls), first)

    def test_delivery_report_is_idempotent(self):
        stub_provider(True)
        client = self.clients[CLIENT_A]
        doc = dispatcher.queue_message(client, "0745788788", "DLR", send_now=True)
        dispatcher.apply_delivery_report(doc.name, "Failed", error="undelivered")
        balance = credit_service.get_balance(client)
        dispatcher.apply_delivery_report(doc.name, "Failed", error="undelivered")
        self.assertEqual(credit_service.get_balance(client), balance)
