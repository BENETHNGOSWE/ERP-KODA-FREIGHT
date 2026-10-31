import frappe

from kodafreight.services import credits as credit_service
from kodafreight.tests import CLIENT_A, KodafreightTestCase


class TestCredits(KodafreightTestCase):
    def test_allocation_deduction_refund(self):
        client = self.clients[CLIENT_A]
        opening = credit_service.get_balance(client)
        credit_service.allocate(client, 50, remarks="unit test")
        self.assertEqual(credit_service.get_balance(client), opening + 50)
        credit_service.deduct(client, 20, remarks="unit test")
        self.assertEqual(credit_service.get_balance(client), opening + 30)
        credit_service.refund(client, 20, remarks="unit test")
        self.assertEqual(credit_service.get_balance(client), opening + 50)

    def test_overdraft_blocked(self):
        client = self.clients[CLIENT_A]
        with self.assertRaises(credit_service.InsufficientCredits):
            credit_service.deduct(client, credit_service.get_balance(client) + 1)

    def test_ledger_matches_account(self):
        client = self.clients[CLIENT_A]
        credit_service.deduct(client, 3)
        before = credit_service.get_summary(client)
        rebuilt = credit_service.rebuild_account(client)
        self.assertEqual(before["balance"], rebuilt["balance"])

    def test_transactions_are_immutable(self):
        client = self.clients[CLIENT_A]
        txn = credit_service.allocate(client, 5, remarks="immutability")
        doc = frappe.get_doc("Kodafreight SMS Credit Transaction", txn.name)
        with self.assertRaises(frappe.ValidationError):
            doc.delete()
