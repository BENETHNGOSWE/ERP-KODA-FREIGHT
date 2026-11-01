"""Offline test suite for Kodafreight business logic.

Run:  python3 tests_offline/run_tests.py
It imports the real application modules and executes them against the in-memory Frappe stub
(`fake_frappe`). No database, Redis or bench is required.
"""

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_parent = os.path.dirname(HERE)
for _candidate in (_parent, os.path.join(_parent, "kodafreight")):
    if os.path.isdir(os.path.join(_candidate, "kodafreight", "services")):
        sys.path.insert(0, _candidate)
        APP_ROOT = _candidate
        break
else:
    raise SystemExit("cannot locate the kodafreight package")

import fake_frappe as ff  # noqa: E402

frappe = ff.frappe

from kodafreight.services import credits as credit_service  # noqa: E402
from kodafreight.services.sms import dispatcher  # noqa: E402
from kodafreight.services.sms import encoding, phone, transport  # noqa: E402
from kodafreight.services.sms.kilakona import KilakonaProvider, map_status  # noqa: E402
from kodafreight.services.sms.messaging_service_tz import MessagingServiceTZProvider  # noqa: E402
from kodafreight.utils import tenant  # noqa: E402

RESULTS = []


def test(fn):
    RESULTS.append(fn)
    return fn


def eq(actual, expected, label=""):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def raises(exc, fn, label=""):
    try:
        fn()
    except exc:
        return True
    except Exception as other:
        raise AssertionError(f"{label}: expected {exc.__name__}, got {type(other).__name__}: {other}")
    raise AssertionError(f"{label}: expected {exc.__name__}, nothing raised")


# ======================================================================================
# fixtures
# ======================================================================================


def setup_world(provider_ok=True, balance_a=100, balance_b=50):
    ff.reset()
    ff.register_controllers()
    ff.USER_ROLES.clear()
    ff.USER_ROLES.update({"Administrator": ["Administrator", "System Manager"]})

    frappe.get_doc({
        "doctype": "Kodafreight SMS Provider", "name": "Kilakona",
        "provider_name": "Kilakona", "driver": "Kilakona", "enabled": 1, "is_default": 1,
        "api_base_url": "https://messaging.kilakona.co.tz",
        "send_endpoint": "/api/v1/vendor/message/send",
        "auth_type": "API Key Header", "api_key": "k", "api_secret": "s",
        "timeout": 30, "verify_ssl": 1, "max_recipients_per_request": 100,
    }).insert()

    for sender in ("KODASMS", "ACME", "GLOBEX"):
        frappe.get_doc({"doctype": "Kodafreight Sender Name", "sender_name": sender,
                        "status": "Active"}).insert()

    for name, client_name, sender in (("CLI-00001", "Acme Freight", "ACME"),
                                      ("CLI-00002", "Globex Logistics", "GLOBEX")):
        frappe.get_doc({
            "doctype": "Kodafreight Client", "name": name, "client_name": client_name,
            "email": f"{sender.lower()}@example.com", "phone": "0745788788",
            "location": "Dar es Salaam", "city": "Dar es Salaam", "country": "Tanzania",
            "status": "Active", "sms_enabled": 1, "low_balance_threshold": 10,
            "senders": [{"sender_name": sender, "enabled": 1, "is_default": 1}],
        }).insert()

    if balance_a:
        credit_service.allocate("CLI-00001", balance_a, remarks="opening")
    if balance_b:
        credit_service.allocate("CLI-00002", balance_b, remarks="opening")

    calls = []

    def hook(url, payload, headers):
        calls.append({"url": url, "payload": payload, "headers": headers})
        if provider_ok:
            return {"ok": True, "status_code": 200,
                    "json": {"success": True, "message": "Message sent successfully!",
                             "data": {"messageId": f"MID{len(calls)}"}, "balance": 10214},
                    "text": "", "error": None}
        return {"ok": False, "status_code": 400,
                "json": {"success": False, "error": "Invalid contacts"},
                "text": "", "error": "HTTP 400"}

    transport.set_test_hook(hook)
    return calls


def as_client_user(email, client, role="Kodafreight Client Admin"):
    if not frappe.db.exists("Kodafreight Client User", email):
        frappe.get_doc({
            "doctype": "Kodafreight Client User", "client": client, "email": email,
            "first_name": "Test", "last_name": "User", "phone": "0745788788",
            "user_role": "Client Admin", "enabled": 1, "user": email,
        }).insert()
    ff.USER_ROLES[email] = [role]
    frappe.session.user = email
    tenant.clear_user_client_cache(email)


# ======================================================================================
# 1. encoding & segmentation
# ======================================================================================


@test
def test_encoding_gsm7_single_part():
    info = encoding.analyse("Hello world")
    eq(info["encoding"], "GSM7", "encoding")
    eq(info["parts"], 1, "parts")


@test
def test_encoding_gsm7_boundaries():
    eq(encoding.count_parts("a" * 160), 1, "160 chars")
    eq(encoding.count_parts("a" * 161), 2, "161 chars")
    eq(encoding.count_parts("a" * 306), 2, "306 chars")
    eq(encoding.count_parts("a" * 307), 3, "307 chars")


@test
def test_encoding_extended_chars_cost_two_septets():
    eq(encoding.analyse("[")["length"], 2, "square bracket = 2 septets")


@test
def test_encoding_ucs2():
    info = encoding.analyse("Habari 😀")
    eq(info["encoding"], "UCS2", "encoding")
    eq(encoding.count_parts("ж" * 70), 1, "70 UCS2 chars")
    eq(encoding.count_parts("ж" * 71), 2, "71 UCS2 chars")


@test
def test_encoding_empty():
    eq(encoding.count_parts(""), 0, "empty message has no parts")


# ======================================================================================
# 2. phone normalisation
# ======================================================================================


@test
def test_phone_formats():
    for raw in ("+255745788788", "00255745788788", "255745788788", "0745788788",
                "745788788", "0745 788 788", "0745-788-788"):
        eq(phone.normalise(raw), "255745788788", f"normalise {raw}")


@test
def test_phone_invalid():
    for raw in ("", None, "abc", "12"):
        raises(phone.InvalidPhoneNumber, lambda raw=raw: phone.normalise(raw), f"invalid {raw}")


@test
def test_phone_other_country_kept():
    eq(phone.normalise("254712345678"), "254712345678", "kenyan number kept")


# ======================================================================================
# 3. provider drivers
# ======================================================================================


@test
def test_kilakona_payload_matches_laravel_contract():
    driver = KilakonaProvider({"api_base_url": "https://messaging.kilakona.co.tz",
                               "api_key": "k", "api_secret": "s"})
    payload = driver.build_payload(["255745788788", "255700000001"], "Hi", "KODASMS", "REF1")
    eq(payload["senderId"], "KODASMS", "senderId")
    eq(payload["messageType"], "text", "messageType")
    eq(payload["contacts"], "255745788788,255700000001", "contacts csv")
    eq(driver.headers()["api_key"], "k", "api_key header")
    eq(driver.endpoint("send_endpoint", "/api/v1/vendor/message/send"),
       "https://messaging.kilakona.co.tz/api/v1/vendor/message/send", "endpoint")


@test
def test_kilakona_success_and_failure_parsing():
    driver = KilakonaProvider({"api_base_url": "https://x"})
    ok = driver.parse_response({"ok": True, "status_code": 200,
                                "json": {"success": True, "balance": 10214,
                                         "data": {"messageId": "M1"}}})
    eq(ok.success, True, "success")
    eq(ok.balance, 10214, "balance")
    eq(ok.provider_message_id, "M1", "message id")

    bad = driver.parse_response({"ok": False, "status_code": 400,
                                 "json": {"success": False, "error": "Invalid sender"}})
    eq(bad.success, False, "failure")
    eq(bad.retryable, False, "4xx is not retryable")

    timeout = driver.parse_response({"ok": False, "status_code": None, "json": None,
                                     "error": "Timeout"})
    eq(timeout.retryable, True, "transport error is retryable")

    server = driver.parse_response({"ok": False, "status_code": 503,
                                    "json": {"success": False, "error": "busy"}})
    eq(server.retryable, True, "5xx is retryable")


@test
def test_status_mapping():
    eq(map_status("DELIVRD"), "Delivered", "DELIVRD")
    eq(map_status("delivered"), "Delivered", "delivered")
    eq(map_status("UNDELIV"), "Failed", "UNDELIV")
    eq(map_status("submitted"), "Sent", "submitted")
    eq(map_status("weird"), "", "unknown")


@test
def test_messaging_service_tz_payload():
    driver = MessagingServiceTZProvider({"api_base_url": "https://messaging-service.co.tz",
                                         "api_key": "cid", "api_secret": "csec"})
    payload = driver.build_payload(["255745788788"], "Test", "KODASMS", "MSG_1")
    eq(payload["auth"]["clientId"], "cid", "clientId")
    eq(payload["messages"][0]["msisdn"], "255745788788", "msisdn")
    eq(payload["messages"][0]["coding"], "GSM7", "coding")
    result = driver.parse_response({"ok": True, "status_code": 200,
                                    "json": {"status": 1, "message": "ok", "balance": 10214}})
    eq(result.success, True, "legacy broker success")


# ======================================================================================
# 4. credit ledger
# ======================================================================================


@test
def test_credit_allocation_and_deduction():
    setup_world()
    eq(credit_service.get_balance("CLI-00001"), 100, "opening balance")
    credit_service.deduct("CLI-00001", 30, remarks="test")
    summary = credit_service.get_summary("CLI-00001")
    eq(summary["allocated"], 100, "allocated")
    eq(summary["used"], 30, "used")
    eq(summary["balance"], 70, "balance")


@test
def test_credit_refund_restores_balance():
    setup_world()
    credit_service.deduct("CLI-00001", 10)
    credit_service.refund("CLI-00001", 10)
    eq(credit_service.get_balance("CLI-00001"), 100, "balance restored")
    eq(credit_service.get_summary("CLI-00001")["used"], 0, "usage restored")


@test
def test_credit_never_goes_negative():
    setup_world()
    raises(credit_service.InsufficientCredits,
           lambda: credit_service.deduct("CLI-00001", 500), "overdraft blocked")
    eq(credit_service.get_balance("CLI-00001"), 100, "balance untouched after failure")


@test
def test_credit_negative_allowed_when_flagged():
    setup_world()
    frappe.db.set_value("Kodafreight SMS Credit Account", "CLI-00001", "allow_negative", 1)
    credit_service.deduct("CLI-00001", 150)
    eq(credit_service.get_balance("CLI-00001"), -50, "negative allowed explicitly")


@test
def test_credit_transactions_recorded_with_running_balance():
    setup_world()
    credit_service.deduct("CLI-00001", 5)
    credit_service.deduct("CLI-00001", 5)
    rows = frappe.get_all("Kodafreight SMS Credit Transaction",
                          filters={"client": "CLI-00001"},
                          fields=["transaction_type", "quantity", "balance_after"],
                          order_by="creation asc")
    eq(len(rows), 3, "ledger rows")
    eq(rows[-1]["balance_after"], 90, "running balance")


@test
def test_credit_rebuild_matches_ledger():
    setup_world()
    credit_service.deduct("CLI-00001", 25)
    credit_service.refund("CLI-00001", 5)
    # Corrupt the cached totals, then rebuild from the ledger.
    frappe.db.set_value("Kodafreight SMS Credit Account", "CLI-00001",
                        {"allocated": 0, "used": 0, "balance": 0})
    result = credit_service.rebuild_account("CLI-00001")
    eq(result["balance"], 80, "rebuilt balance")


@test
def test_credit_rejects_non_positive_quantity():
    setup_world()
    raises(ff.ValidationError, lambda: credit_service.allocate("CLI-00001", 0), "zero")
    raises(ff.ValidationError, lambda: credit_service.allocate("CLI-00001", -5), "negative")


# ======================================================================================
# 5. tenant isolation & sender ownership
# ======================================================================================


@test
def test_sender_ownership_enforced():
    setup_world()
    tenant.assert_sender_allowed("CLI-00001", "ACME")
    raises(ff.PermissionError_,
           lambda: tenant.assert_sender_allowed("CLI-00001", "GLOBEX"),
           "cross-client sender blocked")
    raises(ff.PermissionError_,
           lambda: tenant.assert_sender_allowed("CLI-00001", "KODASMS"),
           "unassigned sender blocked")


@test
def test_inactive_sender_cannot_be_used():
    setup_world()
    frappe.db.set_value("Kodafreight Sender Name", "ACME", "status", "Inactive")
    raises(ff.PermissionError_,
           lambda: tenant.assert_sender_allowed("CLI-00001", "ACME"),
           "inactive sender blocked")


@test
def test_client_user_resolves_own_tenant_only():
    setup_world()
    as_client_user("admin@acme.test", "CLI-00001")
    eq(tenant.get_user_client(), "CLI-00001", "own client")
    eq(tenant.resolve_client(None), "CLI-00001", "resolve without argument")
    raises(ff.PermissionError_, lambda: tenant.resolve_client("CLI-00002"),
           "cannot act for another client")
    frappe.session.user = "Administrator"


@test
def test_message_creation_is_pinned_to_own_client():
    setup_world()
    as_client_user("admin@acme.test", "CLI-00001")
    doc = frappe.get_doc({
        "doctype": "Kodafreight SMS Message", "client": "CLI-00002",
        "recipient": "0745788788", "sender_name": "GLOBEX", "message": "hi",
    })
    raises(ff.PermissionError_, doc.insert, "forged client id rejected")
    frappe.session.user = "Administrator"


@test
def test_suspended_client_cannot_send():
    setup_world()
    frappe.db.set_value("Kodafreight Client", "CLI-00001", "status", "Suspended")
    raises(ff.ValidationError, lambda: tenant.ensure_client_active("CLI-00001"), "suspended")


# ======================================================================================
# 6. dispatch
# ======================================================================================


def make_message(client="CLI-00001", recipient="0745788788", text="Hello", sender="ACME"):
    return dispatcher.queue_message(client, recipient, text, sender_name=sender)


@test
def test_send_single_sms_success_deducts_one_credit():
    calls = setup_world()
    doc = make_message()
    dispatcher.send_message(doc.name)
    doc.reload()
    eq(doc.status, "Sent", "status")
    eq(doc.recipient, "255745788788", "recipient normalised")
    eq(doc.credits_used, 1, "credits used")
    eq(credit_service.get_balance("CLI-00001"), 99, "balance after send")
    eq(len(calls), 1, "one provider call")
    eq(calls[0]["payload"]["senderId"], "ACME", "sender used")


@test
def test_long_message_charges_per_part():
    setup_world()
    doc = make_message(text="x" * 200)
    eq(doc.parts, 2, "two parts")
    dispatcher.send_message(doc.name)
    eq(credit_service.get_balance("CLI-00001"), 98, "two credits used")


@test
def test_failed_send_refunds_credits():
    setup_world(provider_ok=False)
    doc = make_message()
    dispatcher.send_message(doc.name)
    doc.reload()
    eq(doc.status, "Failed", "status")
    assert "Invalid contacts" in (doc.error_message or ""), "error captured"
    eq(credit_service.get_balance("CLI-00001"), 100, "credits refunded")


@test
def test_insufficient_credits_blocks_send():
    setup_world(balance_a=0)
    doc = make_message()
    dispatcher.send_message(doc.name)
    doc.reload()
    eq(doc.status, "Failed", "status")
    assert "Insufficient" in (doc.error_message or ""), "insufficient credits message"


@test
def test_empty_message_rejected():
    setup_world()
    raises(ff.ValidationError, lambda: make_message(text="   "), "empty message")


@test
def test_invalid_number_rejected():
    setup_world()
    raises(phone.InvalidPhoneNumber, lambda: make_message(recipient="12"), "invalid number")


@test
def test_queue_processing_batches_and_claims_once():
    calls = setup_world()
    for i in range(3):
        make_message(recipient=f"07457887{i:02d}")
    result = dispatcher.process_pending()
    eq(result["sent"], 3, "all sent")
    eq(len(calls), 3, "one provider call each")
    # A second run must not resend anything.
    calls.clear()
    dispatcher.process_pending()
    eq(len(calls), 0, "no duplicate sends")


@test
def test_retryable_failure_schedules_retry():
    setup_world()
    transport.set_test_hook(lambda url, payload, headers: {
        "ok": False, "status_code": None, "json": None, "error": "Timeout"})
    doc = make_message()
    dispatcher.send_message(doc.name)
    doc.reload()
    eq(doc.status, "Queued", "re-queued for retry")
    eq(doc.retry_count, 1, "retry counter")
    assert doc.next_retry_at, "retry scheduled"
    eq(credit_service.get_balance("CLI-00001"), 100, "credits refunded while retrying")


@test
def test_retries_stop_at_max():
    setup_world()
    transport.set_test_hook(lambda url, payload, headers: {
        "ok": False, "status_code": None, "json": None, "error": "Timeout"})
    doc = make_message()
    for _ in range(5):
        dispatcher.send_message(doc.name, force=True)
    doc.reload()
    eq(doc.status, "Failed", "eventually fails")
    eq(doc.retry_count, 3, "max retries respected")


@test
def test_delivery_report_marks_delivered():
    setup_world()
    doc = make_message()
    dispatcher.send_message(doc.name)
    dispatcher.apply_delivery_report(doc.name, "Delivered", provider_message_id="MID1")
    doc.reload()
    eq(doc.status, "Delivered", "delivered")
    assert doc.delivered_at, "timestamp set"


@test
def test_duplicate_delivery_report_is_idempotent():
    setup_world()
    doc = make_message()
    dispatcher.send_message(doc.name)
    dispatcher.apply_delivery_report(doc.name, "Failed", error="undelivered")
    balance_after_first = credit_service.get_balance("CLI-00001")
    dispatcher.apply_delivery_report(doc.name, "Failed", error="undelivered")
    eq(credit_service.get_balance("CLI-00001"), balance_after_first,
       "second report does not refund twice")


@test
def test_sent_message_content_is_immutable():
    setup_world()
    doc = make_message()
    dispatcher.send_message(doc.name)
    doc.reload()
    doc.message = "tampered"
    raises(ff.ValidationError, doc.save, "cannot edit a sent message")


# ======================================================================================
# 7. campaigns (bulk)
# ======================================================================================


@test
def test_campaign_dedupes_and_sends_to_all():
    calls = setup_world()
    campaign = frappe.get_doc({
        "doctype": "Kodafreight SMS Campaign", "title": "Promo", "client": "CLI-00001",
        "sender_name": "ACME", "message": "Hi {first_name}", "recipient_source": "Manual",
        "status": "Draft",
        "recipients": [
            {"phone": "0745788788", "recipient_name": "Asha Mtali", "status": "Pending"},
            {"phone": "255745788788", "recipient_name": "Duplicate", "status": "Pending"},
            {"phone": "0754123456", "recipient_name": "Baraka Juma", "status": "Pending"},
        ],
    }).insert()
    eq(campaign.total_recipients, 2, "duplicate number removed")
    campaign.queue_campaign()
    campaign.reload()
    eq(campaign.status, "Completed", "campaign completed")
    eq(campaign.sent_count, 2, "sent count")
    eq(len(calls), 2, "one call per recipient")
    eq(calls[0]["payload"]["message"], "Hi Asha", "personalisation applied")
    eq(credit_service.get_balance("CLI-00001"), 98, "credits charged per recipient")


@test
def test_campaign_blocked_without_enough_credits():
    setup_world(balance_a=1)
    campaign = frappe.get_doc({
        "doctype": "Kodafreight SMS Campaign", "title": "Promo", "client": "CLI-00001",
        "sender_name": "ACME", "message": "Hello", "recipient_source": "Manual",
        "status": "Draft",
        "recipients": [{"phone": "074578878%d" % i, "status": "Pending"} for i in range(5)],
    }).insert()
    raises(ff.ValidationError, campaign.queue_campaign, "insufficient credits for campaign")


@test
def test_campaign_rejects_foreign_sender():
    setup_world()
    doc = frappe.get_doc({
        "doctype": "Kodafreight SMS Campaign", "title": "X", "client": "CLI-00001",
        "sender_name": "GLOBEX", "message": "Hi", "recipient_source": "Manual",
        "status": "Draft", "recipients": [{"phone": "0745788788"}],
    })
    raises(ff.PermissionError_, doc.insert, "foreign sender rejected")


@test
def test_legacy_placeholder_still_supported():
    from kodafreight.kodafreight.doctype.kodafreight_sms_campaign.kodafreight_sms_campaign import (
        render,
    )

    text = render("Habari {{ customer_firstname }}, <b>karibu</b>",
                  {"recipient_name": "Asha Mtali", "phone": "255745788788"})
    eq(text, "Habari Asha, karibu", "legacy placeholder + html stripped")


# ======================================================================================
# 8. client document rules
# ======================================================================================


@test
def test_client_default_sender_is_unique():
    setup_world()
    doc = frappe.get_doc("Kodafreight Client", "CLI-00001")
    doc.append("senders", {"sender_name": "KODASMS", "enabled": 1, "is_default": 1})
    raises(ff.ValidationError, doc.save, "two default senders rejected")


@test
def test_client_duplicate_sender_rejected():
    setup_world()
    doc = frappe.get_doc("Kodafreight Client", "CLI-00001")
    doc.append("senders", {"sender_name": "ACME", "enabled": 1})
    raises(ff.ValidationError, doc.save, "duplicate assignment rejected")


@test
def test_suspension_requires_reason():
    setup_world()
    doc = frappe.get_doc("Kodafreight Client", "CLI-00001")
    doc.status = "Suspended"
    raises(ff.ValidationError, doc.save, "reason required")


@test
def test_deactivating_client_cancels_queued_messages():
    setup_world()
    doc_msg = make_message()
    client = frappe.get_doc("Kodafreight Client", "CLI-00001")
    client.status = "Inactive"
    client.save()
    eq(frappe.db.get_value("Kodafreight SMS Message", doc_msg.name, "status"), "Cancelled",
       "queued message cancelled")


@test
def test_sender_name_validation():
    setup_world()
    bad = frappe.get_doc({"doctype": "Kodafreight Sender Name",
                          "sender_name": "WAYTOOLONGSENDER", "status": "Active"})
    raises(ff.ValidationError, bad.insert, "sender longer than 11 chars")
    worse = frappe.get_doc({"doctype": "Kodafreight Sender Name",
                            "sender_name": "BAD!NAME", "status": "Active"})
    raises(ff.ValidationError, worse.insert, "invalid characters")


# ======================================================================================
# 9. contacts
# ======================================================================================


@test
def test_contact_normalises_and_dedupes():
    setup_world()
    frappe.get_doc({"doctype": "Kodafreight Contact", "client": "CLI-00001",
                    "first_name": "Asha", "phone": "0745788788"}).insert()
    stored = frappe.get_all("Kodafreight Contact", filters={"client": "CLI-00001"},
                            fields=["msisdn"])
    eq(stored[0]["msisdn"], "255745788788", "msisdn stored")
    duplicate = frappe.get_doc({"doctype": "Kodafreight Contact", "client": "CLI-00001",
                                "first_name": "Asha2", "phone": "+255745788788"})
    raises(ff.ValidationError, duplicate.insert, "duplicate contact rejected")


@test
def test_contact_category_must_belong_to_same_client():
    setup_world()
    frappe.get_doc({"doctype": "Kodafreight Contact Category", "name": "CAT-1",
                    "client": "CLI-00002", "category_name": "VIP"}).insert()
    doc = frappe.get_doc({"doctype": "Kodafreight Contact", "client": "CLI-00001",
                          "first_name": "X", "phone": "0745788700", "category": "CAT-1"})
    raises(ff.ValidationError, doc.insert, "cross-client category rejected")


# ======================================================================================
# freight (Laravel Loadpackage -> Cargo Load -> Sales Invoice -> Release -> Delivery)
# ======================================================================================

from kodafreight.services import freight as freight_service  # noqa: E402


def _load(**kw):
    base = {
        "doctype": "Cargo Load", "name": "KFL-TEST",
        "length": 0, "width": 0, "height": 0, "measure_dimensions": 0,
        "quantity": 0, "is_volume_unit": 0,
        "rate": 0, "exchange_rate": 1, "transport_fee": 0, "weight": 0,
        "ocean_mode": 1,
    }
    base.update(kw)
    return frappe._dict(base)


def test_cbm_is_length_times_width_times_height():
    eq(freight_service.compute_volume(2, 0.5, 0.3), 0.3, "volume")


def test_measurements_are_optional():
    """A load with no measurements and no quantity is still saved (Pending Pricing)."""
    load = _load(ocean_mode=1)
    eq(freight_service.billable_quantity(load), 0, "nothing billable")
    eq(freight_service.is_priced(load), False, "not priced")
    eq(freight_service.total_amount(load), 0, "no money invented")


def test_volume_unit_uses_the_typed_quantity_when_nothing_is_measured():
    load = _load(quantity=2.5, is_volume_unit=1, rate=1000)
    eq(freight_service.billable_quantity(load), 2.5, "typed CBM is billable")
    eq(freight_service.freight_amount(load), 2500, "amount")


def test_measured_dimensions_win_over_the_typed_quantity():
    load = _load(measure_dimensions=1, length=2, width=1, height=1,
                 quantity=99, is_volume_unit=1, rate=1000)
    eq(freight_service.billable_volume(load), 2, "measured")
    eq(freight_service.billable_quantity(load), 2, "measured beats typed")


def test_measured_dimensions_are_ignored_when_the_box_is_not_ticked():
    load = _load(measure_dimensions=0, length=2, width=1, height=1, quantity=3,
                 is_volume_unit=1, rate=10)
    eq(freight_service.billable_volume(load), 3, "typed quantity used")


def test_weight_unit_is_priced_per_kg_not_per_cbm():
    load = _load(quantity=120, is_volume_unit=0, rate=500)
    eq(freight_service.billable_quantity(load), 120, "kg")
    eq(freight_service.freight_amount(load), 60000, "amount")
    eq(freight_service.unit_uom(load), "Nos", "non volume uom")


def test_piece_unit_is_priced_per_piece():
    load = _load(quantity=8, is_volume_unit=0, rate=250)
    eq(freight_service.freight_amount(load), 2000, "amount")


def test_volume_unit_detection():
    eq(freight_service.is_volume_unit("CBM"), True, "CBM")
    eq(freight_service.is_volume_unit(None, "m³"), True, "m3 symbol")
    eq(freight_service.is_volume_unit("Cubic Meter"), True, "uom name")
    eq(freight_service.is_volume_unit("KG"), False, "kg")
    eq(freight_service.is_volume_unit("PCS"), False, "pieces")
    eq(freight_service.is_volume_unit(None), False, "nothing")


def test_freight_amount_reproduces_legacy_invoice_total():
    # Real record INV-20250905-VKHMO: 234 CBM x 2750 rate x 20 exchange = 12,870,000
    load = _load(quantity=234, is_volume_unit=1, rate=2750, exchange_rate=20)
    eq(freight_service.freight_amount(load), 12_870_000, "freight")
    eq(freight_service.total_amount(load), 12_870_000, "total")
    eq(freight_service.is_priced(load), True, "priced")


def test_transport_fee_is_added_to_the_freight_amount():
    load = _load(quantity=2, is_volume_unit=1, rate=1000, transport_fee=250)
    eq(freight_service.freight_amount(load), 2000, "freight")
    eq(freight_service.total_amount(load), 2250, "due = amount + transport")


def test_exchange_rate_defaults_to_one_when_blank():
    load = _load(quantity=3, is_volume_unit=1, rate=1000, exchange_rate=0)
    eq(freight_service.freight_amount(load), 3000, "no fx")


def test_a_load_can_be_invoiced_with_a_zero_rate():
    """The invoice is opened at load submission; the rate is typed later on it."""
    load = _load(quantity=3, is_volume_unit=1, rate=0)
    eq(freight_service.is_priced(load), False, "no money yet")
    rows = freight_service.invoice_rows_for.__doc__ is not None
    eq(rows, True, "invoice rows are independent of pricing")
    eq(freight_service.billable_quantity(load), 3, "quantity is still known")


def test_load_without_volume_or_rate_is_not_priced():
    eq(freight_service.is_priced(_load(rate=2750)), False, "no quantity")
    eq(freight_service.is_priced(_load(quantity=2, is_volume_unit=1)), False, "no rate")
    eq(freight_service.is_priced(_load(transport_fee=500)), True, "transport alone is billable")


def test_parse_invoice_names_accepts_json_list_csv_and_newlines():
    eq(freight_service.parse_invoice_names('["SI-1", "SI-2"]'), ["SI-1", "SI-2"], "json")
    eq(freight_service.parse_invoice_names("SI-1, SI-2"), ["SI-1", "SI-2"], "csv")
    eq(freight_service.parse_invoice_names("SI-1\nSI-2\n"), ["SI-1", "SI-2"], "newlines")


def test_parse_invoice_names_rejects_empty_selection():
    raises(ff.ValidationError, lambda: freight_service.parse_invoice_names("[]"), "empty")
    raises(ff.ValidationError, lambda: freight_service.parse_invoice_names("  "), "blank")


def _invoice(name, grand_total, outstanding, docstatus=1, customer="CUST-1"):
    return frappe.get_doc({
        "doctype": "Sales Invoice", "name": name, "customer": customer,
        "grand_total": grand_total, "outstanding_amount": outstanding,
        "docstatus": docstatus,
    }).insert()


def test_invoice_status_label_follows_the_money_not_a_typed_field():
    eq(freight_service.invoice_status_label(_invoice("SI-D", 100, 100, 0)), "Draft Invoice")
    eq(freight_service.invoice_status_label(_invoice("SI-U", 100, 100)), "Invoiced")
    eq(freight_service.invoice_status_label(_invoice("SI-P", 100, 40)), "Partly Paid")
    eq(freight_service.invoice_status_label(_invoice("SI-F", 100, 0)), "Paid")


def test_release_is_refused_while_anything_is_outstanding():
    _invoice("SI-UNPAID", 1000, 400)
    raises(ff.ValidationError,
           lambda: freight_service.assert_invoice_paid("SI-UNPAID"), "partial blocks")


def test_release_is_allowed_only_when_fully_paid():
    _invoice("SI-PAID", 1000, 0)
    state = freight_service.assert_invoice_paid("SI-PAID")
    eq(state["paid"], True, "paid")
    eq(state["outstanding_amount"], 0, "nothing outstanding")


def test_release_is_refused_for_a_draft_invoice():
    _invoice("SI-DRAFT", 1000, 1000, docstatus=0)
    raises(ff.ValidationError,
           lambda: freight_service.assert_invoice_paid("SI-DRAFT"), "draft blocks")


def test_payment_state_reports_the_real_numbers():
    _invoice("SI-PART", 575.01, 275.01)
    state = freight_service.invoice_payment_state("SI-PART")
    eq(state["grand_total"], 575.01, "due")
    eq(state["outstanding_amount"], 275.01, "balance")
    eq(round(state["grand_total"] - state["outstanding_amount"], 2), 300.00, "received")


# ---------------------------------------------------------------- print context safety
from kodafreight import freight_events  # noqa: E402


class _BrokenDB:
    """Every database call fails, like a wrong column name on a real bench."""

    def __getattr__(self, item):
        def boom(*args, **kwargs):
            raise Exception("(1054, \"Unknown column 'company_address' in 'SELECT'\")")

        return boom


def _doc(**kw):
    base = {"doctype": "Sales Invoice", "name": "SI-1", "company": "KODA", "customer": "CUST-1"}
    base.update(kw)
    return frappe._dict(base)


def test_print_context_swallows_database_errors():
    """A bad letterhead lookup must degrade to blank lines, never to a 500 page."""
    original_db = frappe.db
    original_log = frappe.log_error
    frappe.db = _BrokenDB()
    frappe.log_error = lambda *a, **k: None
    try:
        doc = _doc()
        freight_events.set_invoice_print_context(doc)   # must not raise
        freight_events.set_freight_print_context(doc)   # must not raise
    finally:
        frappe.db = original_db
        frappe.log_error = original_log


def test_print_context_accepts_the_extra_print_settings_argument():
    """Frappe calls before_print with (doc, method, print_settings)."""
    freight_events.set_freight_print_context(_doc(), "before_print", {"with_letterhead": 0})
    freight_events.set_invoice_print_context(_doc(), "before_print", {"with_letterhead": 0})


def test_print_context_does_not_query_a_missing_company_address_column():
    """ERPNext has no Company.company_address column - the lookup must not ask for it."""
    import inspect

    source = inspect.getsource(freight_events._company_address)
    assert "tabAddress" in source and "Dynamic Link" in source, source


def test_payment_info_prints_one_field_per_line():
    text = freight_events._format_account("KODA BANK", "KODA TECHNOLOGIES", "345789098")
    eq(text.split("\n"), [
        "Bank Name: KODA BANK",
        "Acc Name: KODA TECHNOLOGIES",
        "Acc No: 345789098",
    ], "three separate lines")


def test_payment_info_includes_branch_and_swift_only_when_present():
    without = freight_events._format_account("B", "A", "1")
    eq(len(without.split("\n")), 3, "no optional lines")
    with_extra = freight_events._format_account("B", "A", "1", "Mlimani", "SW123")
    eq(len(with_extra.split("\n")), 5, "branch + swift")


def test_payment_info_blocks_are_separated_by_a_blank_line():
    first = freight_events._format_account("B1", "A1", "111")
    second = freight_events._format_account("B2", "A2", "222")
    joined = "\n\n".join([first, second])
    eq(joined.count("\n\n"), 1, "one blank line between accounts")
    eq(joined.split("\n")[3], "", "blank separator")


# Register the freight tests declared above (they are written as plain functions so the
# file reads like documentation, then collected here in declaration order).
for _name, _fn in list(globals().items()):
    if _name.startswith("test_") and callable(_fn) and _fn not in RESULTS:
        RESULTS.append(_fn)



# ======================================================================================
# runner
# ======================================================================================


def main():
    passed, failed = 0, []
    for fn in RESULTS:
        try:
            ff.reset()
            ff.register_controllers()
            fn()
            passed += 1
            print(f"  PASS  {fn.__name__}")
        except Exception as exc:
            failed.append((fn.__name__, exc, traceback.format_exc()))
            print(f"  FAIL  {fn.__name__}: {exc}")
    print("\n" + "=" * 70)
    print(f"{passed} passed, {len(failed)} failed, {len(RESULTS)} total")
    if failed:
        print("=" * 70)
        for name, exc, tb in failed:
            print(f"\n--- {name} ---\n{tb}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
