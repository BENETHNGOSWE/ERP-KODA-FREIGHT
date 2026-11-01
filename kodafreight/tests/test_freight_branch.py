"""Bench tests for the first freight setup DocType; no ERPNext required."""

import frappe

from kodafreight.tests import CLIENT_A, CLIENT_B, KodafreightTestCase


class TestFreightBranch(KodafreightTestCase):
    def _make_user(self, client):
        email = "_test_kf_branch_a@example.com"
        if not frappe.db.exists("Kodafreight Client User", email):
            frappe.get_doc({
                "doctype": "Kodafreight Client User", "client": client,
                "first_name": "Branch", "last_name": "Admin", "email": email,
                "phone": "0745788700", "user_role": "Client Admin", "enabled": 1,
            }).insert()
        frappe.db.commit()
        return email

    def _branch(self, client, code):
        return frappe.get_doc({
            "doctype": "Branch", "client": client,
            "branch_name": "Branch " + code, "branch_code": code,
        }).insert()

    def test_client_only_lists_own_branches(self):
        a, b = self.clients[CLIENT_A], self.clients[CLIENT_B]
        own, other = self._branch(a, "TESTA"), self._branch(b, "TESTB")
        user = self._make_user(a)
        frappe.set_user(user)
        names = set(frappe.get_list("Branch", pluck="name"))
        self.assertIn(own.name, names)
        self.assertNotIn(other.name, names)
        self.assertFalse(frappe.has_permission("Branch", "read", doc=other.name))

    def test_client_cannot_create_branch_for_other_client(self):
        a, b = self.clients[CLIENT_A], self.clients[CLIENT_B]
        frappe.set_user(self._make_user(a))
        doc = frappe.get_doc({"doctype": "Branch", "client": b,
                              "branch_name": "Foreign", "branch_code": "F"})
        with self.assertRaises(frappe.PermissionError):
            doc.insert()

    def test_existing_branch_cannot_be_moved_to_another_client(self):
        a, b = self.clients[CLIENT_A], self.clients[CLIENT_B]
        doc = self._branch(a, "MOVE")
        doc.client = b
        with self.assertRaises(frappe.PermissionError):
            doc.save()
