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
