# Phase 9 — SMS provider integration

## 1. What the Laravel system actually uses

Determined from `app/Helpers/ControllerHelper.php` (the only live send path), `.env` and
`sms_log.txt`.

| Item | Value |
|---|---|
| Provider | **Kilakona** (`messaging.kilakona.co.tz`) |
| API URL | `https://messaging.kilakona.co.tz/api/v1/vendor/message/send` |
| Method | `POST`, JSON body |
| Authentication | Headers `api_key: $SMS_API_KEY2`, `api_secret: $SMS_API_SECRET2` |
| Request | `{"senderId": "...", "messageType": "text", "message": "...", "contacts": "2557...,2557..."}` |
| Success | `{"success": true, ...}` → Laravel returns `['success'=>true]` |
| Failure | `{"success": false, "error": "..."}` → `['success'=>false, 'message'=>'Failed to send message: <error>']` |
| Sender handling | Free-text string, validated only as `max:11` at the controller, **never checked against the client's assignments** |
| Number format | Whatever the operator typed; a comma-joined list is sent as `contacts` |
| Delivery callback | **None** |
| Timeout | **None set** (cURL default, can hang the PHP worker) |
| TLS | `CURLOPT_SSL_VERIFYPEER = false` |
| Retry | **None** (a failed row simply stays `failed`) |
| Balance | Not read back |

A second, older integration is still visible in the code base and in `sms_log.txt`
(`messaging-service.co.tz`, "NextSMS"-style broker):

```json
{"auth":{"clientId":"…","clientSecret":"…"},
 "messages":[{"text":"…","msisdn":"255745788788","source":"KODASMS",
              "reference":"MSG_1745402550_0_6808bab663d05","coding":"GSM7"}]}
→ {"status":1,"message":"Successfully submitted 1 message(s) to broker.","data":null,"balance":10214}
```

## 2. How Kodafreight implements it

```
kodafreight/services/sms/
├── base.py                  # BaseProvider + SendResult contract
├── transport.py             # single HTTP entry point (timeout, TLS, error → SendResult)
├── kilakona.py              # default driver (parity with the Laravel helper)
├── messaging_service_tz.py  # legacy broker driver (auth/messages payload)
├── factory.py               # DocType → driver resolution
├── encoding.py              # GSM7/UCS2 segmentation
├── phone.py                 # MSISDN normalisation
└── dispatcher.py            # business pipeline (credits, sender check, status, retries)
```

Credentials live in the **`Kodafreight SMS Provider`** DocType; `api_secret` uses the
`Password` fieldtype, so Frappe stores it encrypted in `__Auth` and it never appears in a
fixture, an export or the ZIP.

### Send pipeline (`dispatcher.send_message`)

1. `ensure_client_active` — the tenant must be `Active` with `sms_enabled`.
2. `assert_sender_allowed` — the sender name must be assigned to the tenant **and** active.
3. `phone.normalise` — `0745788788` → `255745788788` (country code and national length come
   from `Kodafreight Settings`).
4. `encoding.analyse` — GSM-7 (160/153) or UCS-2 (70/67) → `parts`, and `credits_used`.
5. `credits.deduct` — row-locked, before the API call, refunded automatically on failure.
6. Driver call — `POST` with the exact Kilakona contract, `timeout` from settings,
   `verify=True` by default, and a `reference` (`KF_<message name>`) used as the
   idempotency/reconciliation key.
7. Result handling:
   * success → `Sent`, `sent_at`, `provider_message_id`, raw response stored;
   * business failure (4xx / `success:false`) → `Failed`, error text stored, credits refunded;
   * transport error / 5xx → `Queued` again with `retry_count += 1` and
     `next_retry_at = now + retry_backoff_minutes`, up to `max_retries` (default 3).
8. `audit.log` — every outcome is written to `Kodafreight Audit Log`.

### Delivery callbacks

`POST /api/method/kodafreight.api.webhooks.delivery_report`

* Requires header `X-Kodafreight-Signature`: HMAC-SHA256 of the raw body keyed with
  `Kodafreight Settings.webhook_secret` (the plain secret is also accepted for providers that
  cannot sign).
* Optional IP allowlist.
* The payload is normalised by the driver's `parse_callback` and mapped through
  `map_status` (`DELIVRD/delivered/success → Delivered`, `UNDELIV/failed/rejected/expired →
  Failed`, `submitted/accepted/pending → Sent`).
* A `dedupe_key = sha256(provider_message_id|reference|raw_status|message)` is stored on
  `Kodafreight SMS Delivery Log` with a unique index, so a provider that retries the same
  report cannot double-refund credits (verified by the test
  `test_duplicate_delivery_report_is_idempotent`).
* A confirmed `Failed` delivery report refunds the credits that were charged at dispatch.

`POST /api/method/kodafreight.api.webhooks.inbound_message` accepts MO traffic and files it
against the `Kodafreight SMS Number` that owns the destination.

### Balance

The Kilakona response carries a `balance` field; it is captured in `SendResult.balance` and
stored inside `provider_response`. Kodafreight bills against its own ledger (per client), so
the provider balance is informational — it is the *platform* pool, not the tenant's.

### Switching or adding providers

Create another `Kodafreight SMS Provider` record and select the driver
(`Kilakona`, `Messaging Service TZ`, `Generic HTTP`). A client can override the platform
default with `Kodafreight Client.provider`. Adding a new driver means one file implementing
`send()` and `parse_callback()` plus one entry in `factory.DRIVERS`.
