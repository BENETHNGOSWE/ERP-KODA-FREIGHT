"""Frappe-native integration tests.

Run inside a bench:

    bench --site <site> run-tests --app kodafreight

These exercise the same rules as `tests_offline/run_tests.py`, but against a real database,
real permissions and real background jobs. The provider HTTP call is stubbed through
`kodafreight.services.sms.transport.set_test_hook`, so no SMS is ever sent by the suite.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from kodafreight.services import credits as credit_service
from kodafreight.services.sms import dispatcher, transport

CLIENT_A = "_Test Kodafreight Client A"
CLIENT_B = "_Test Kodafreight Client B"


def stub_provider(success=True, calls=None):
    def hook(url, payload, headers):
        if calls is not None:
            calls.append({"url": url, "payload": payload})
        if success:
            return {"ok": True, "status_code": 200,
                    "json": {"success": True, "data": {"messageId": "TESTMID"},
                             "balance": 999},
                    "text": "", "error": None}
        return {"ok": False, "status_code": 400,
                "json": {"success": False, "error": "rejected"}, "text": "",
                "error": "HTTP 400"}

    transport.set_test_hook(hook)


class KodafreightTestCase(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.clients = {}
        for label, sender in ((CLIENT_A, "TESTA"), (CLIENT_B, "TESTB")):
            if not frappe.db.exists("Kodafreight Sender Name", sender):
                frappe.get_doc({"doctype": "Kodafreight Sender Name",
                                "sender_name": sender, "status": "Active"}).insert()
            existing = frappe.db.get_value("Kodafreight Client", {"client_name": label}, "name")
            if existing:
                cls.clients[label] = existing
                continue
            doc = frappe.get_doc({
                "doctype": "Kodafreight Client",
                "client_name": label,
                "email": f"{sender.lower()}@example.com",
                "phone": "0745788788",
                "location": "Dar es Salaam",
                "city": "Dar es Salaam",
                "country": "Tanzania",
                "status": "Active",
                "sms_enabled": 1,
                "senders": [{"sender_name": sender, "enabled": 1, "is_default": 1}],
            }).insert()
            cls.clients[label] = doc.name
            credit_service.allocate(doc.name, 100, remarks="test opening")
        frappe.db.commit()

    def tearDown(self):
        transport.set_test_hook(None)
        frappe.set_user("Administrator")
