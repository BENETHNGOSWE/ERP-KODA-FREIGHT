import frappe

from kodafreight.services.sms import dispatcher
from kodafreight.tests import CLIENT_A, CLIENT_B, KodafreightTestCase, stub_provider


class TestTenantIsolation(KodafreightTestCase):
    def make_user(self, client, email, role="Client Admin"):
        if frappe.db.exists("Kodafreight Client User", email):
            return email
        frappe.get_doc({
            "doctype": "Kodafreight Client User",
            "client": client,
            "first_name": "Test",
            "last_name": "User",
            "email": email,
            "phone": "0745788700",
            "user_role": role,
            "enabled": 1,
        }).insert()
        frappe.db.commit()
        return email

    def test_client_cannot_list_other_client_messages(self):
        stub_provider(True)
        a, b = self.clients[CLIENT_A], self.clients[CLIENT_B]
        dispatcher.queue_message(a, "0745788788", "A message", send_now=True)
        dispatcher.queue_message(b, "0745788799", "B message", send_now=True)
        frappe.db.commit()

        user_a = self.make_user(a, "_test_kf_a@example.com")
        frappe.set_user(user_a)
        rows = frappe.get_all("Kodafreight SMS Message", fields=["name", "client"])
        self.assertTrue(rows)
        self.assertTrue(all(r.client == a for r in rows))

    def test_client_cannot_read_other_client_document(self):
        a, b = self.clients[CLIENT_A], self.clients[CLIENT_B]
        stub_provider(True)
        other = dispatcher.queue_message(b, "0745788799", "secret", send_now=True)
        frappe.db.commit()
        frappe.set_user(self.make_user(a, "_test_kf_a@example.com"))
        self.assertFalse(
            frappe.has_permission("Kodafreight SMS Message", "read", doc=other.name)
        )

    def test_client_only_sees_own_sender_names(self):
        a = self.clients[CLIENT_A]
        frappe.set_user(self.make_user(a, "_test_kf_a@example.com"))
        senders = frappe.get_all("Kodafreight Sender Name", pluck="name")
        self.assertIn("TESTA", senders)
        self.assertNotIn("TESTB", senders)

    def test_client_cannot_create_message_for_other_client(self):
        a, b = self.clients[CLIENT_A], self.clients[CLIENT_B]
        frappe.set_user(self.make_user(a, "_test_kf_a@example.com"))
        doc = frappe.get_doc({
            "doctype": "Kodafreight SMS Message",
            "client": b,
            "recipient": "0745788788",
            "sender_name": "TESTB",
            "message": "cross tenant",
        })
        with self.assertRaises(frappe.PermissionError):
            doc.insert()

    def test_user_permission_created_for_client_user(self):
        a = self.clients[CLIENT_A]
        email = self.make_user(a, "_test_kf_a@example.com")
        user = frappe.db.get_value("Kodafreight Client User", email, "user")
        self.assertTrue(frappe.db.exists("User Permission", {
            "user": user, "allow": "Kodafreight Client", "for_value": a}))
