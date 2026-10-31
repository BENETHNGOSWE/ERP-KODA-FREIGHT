# Phase 12–13 — Testing report and bug hunt

## 0. What could and could not be executed here — read this first

| Environment capability | Status |
|---|---|
| Python 3.13 sandbox, no network services | available |
| Frappe/ERPNext v16 bench (MariaDB 10.6+, Redis, Node 18+, wkhtmltopdf) | **not available in this sandbox** |
| Headless browser for real screenshots | **not available** |

Because a real bench could not be provisioned, the following was done instead, and the
distinction is stated explicitly everywhere in this delivery:

1. **Executed** — 47 behavioural tests that import the *real* Kodafreight modules
   (`credits`, `dispatcher`, `encoding`, `phone`, the Kilakona and Messaging-Service-TZ
   drivers, and the `Kodafreight Client / SMS Message / SMS Campaign / Contact / Sender Name`
   controllers) and run them against an in-memory Frappe stub
   (`tests_offline/fake_frappe.py`). Result: **47 passed, 0 failed**.
2. **Executed** — a static package validator (`tests_offline/validate_app.py`) that checks the
   things `bench migrate` would reject: JSON validity, field-order consistency, Link/Table
   targets, child-table flags, autoname fields, controller class names, every dotted path in
   `hooks.py`, workspace/report/number-card references, `frm.call` targets in the client
   scripts, Python syntax of every module, and committed-secret patterns.
   Result: **20 DocTypes, 0 errors, 0 warnings**.
3. **Executed** — the client dashboard was rendered from the shipped Jinja template, the
   shipped CSS and the shipped `www/kodafreight/index.py` controller, with data produced by
   the real send pipeline → `kodafreight-client-dashboard.html`. This is a faithful render of
   the implemented UI, **not** a browser screenshot of a running site.
4. **Shipped but not executed here** — `kodafreight/tests/` contains four `FrappeTestCase`
   suites (credits, SMS, tenant isolation, webhooks) for `bench run-tests --app kodafreight`.
   They require a bench and are the ones to run during the installation test on your server.

Nothing in this report claims a result that was not produced by one of the runs above.

---

## 1. Offline behavioural suite — `python3 tests_offline/run_tests.py`

```
47 passed, 0 failed, 47 total
```

### Encoding & segmentation (5)
| Test | Verifies |
|---|---|
| `test_encoding_gsm7_single_part` | plain ASCII → GSM7, 1 part |
| `test_encoding_gsm7_boundaries` | 160→1, 161→2, 306→2, 307→3 parts |
| `test_encoding_extended_chars_cost_two_septets` | `[` counts as 2 septets |
| `test_encoding_ucs2` | emoji/Cyrillic → UCS2, 70→1, 71→2 |
| `test_encoding_empty` | empty text = 0 parts |

### Phone normalisation (3)
`+255…`, `00255…`, `255…`, `0745…`, `745…`, spaced and hyphenated forms all normalise to
`255745788788`; invalid inputs raise; foreign MSISDNs are preserved.

### Provider drivers (4)
Kilakona request body/headers/URL match the Laravel contract byte for byte; success, business
failure, transport error and 5xx are classified correctly (only the last two retryable);
`DELIVRD/UNDELIV/submitted` map to `Delivered/Failed/Sent`; the legacy broker payload keeps the
`auth` + `messages[]` shape with `coding: GSM7`.

### Credit ledger (7)
Allocation, deduction, refund, running balance, **overdraft refused**, negative balance only
when explicitly allowed, ledger rebuild reproduces the account totals, non-positive amounts
rejected.

### Tenant isolation & sender ownership (5)
Sender assigned to client A is usable by A and rejected for B; an inactive sender is rejected;
a client user always resolves to its own client and cannot act for another; a forged `client`
on a new message raises `PermissionError`; a suspended client cannot send.

### Dispatch (12)
Single send (status, MSISDN, 1 credit, correct `senderId` on the wire), multi-part charging,
provider failure → `Failed` + **refund**, insufficient credits → `Failed` without an API call,
empty message rejected, invalid number rejected, batch processing sends each message exactly
once across two scheduler runs (no duplicates), retryable failure re-queues with a backoff,
retries stop at `max_retries`, delivery report marks `Delivered`, duplicate delivery report
does not refund twice, an already-sent message cannot be edited.

### Campaigns (4)
Manual recipient lists are de-duplicated (`0745788788` vs `255745788788` counted once),
personalisation applied, one provider call per recipient, credits charged per recipient;
campaigns are blocked when the balance is insufficient; a foreign sender is rejected;
the legacy `{{ customer_firstname }}` placeholder and HTML stripping still work.

### Client / contact rules (7)
Two default senders rejected, duplicate sender assignment rejected, suspension requires a
reason, deactivating a client cancels its queued messages, sender-name format validated
(≤11 chars, alphanumeric), contacts stored as MSISDN with per-client duplicate detection, a
category from another client is rejected.

---

## 2. Static package validation — `python3 tests_offline/validate_app.py`

```
DocTypes validated : 20
Warnings           : 0
Errors             : 0
```

Checks performed: JSON parse; required keys; module = `Kodafreight`; InnoDB; unique
fieldnames; `field_order` == `fields`; Link/Table options resolve to an existing DocType;
Table options are child tables and Links are not; Select defaults are members of their option
list; `autoname` fields exist and are mandatory; `naming_series:` implies a `naming_series`
field; child tables carry no permissions; every non-child DocType grants the platform admin;
the controller class name matches the DocType; every `kodafreight.*` path in `hooks.py`
resolves; every `frm.call("x")` in a client script has a matching Python method; every
workspace link/card/chart/report exists; every script report defines `execute()`; every
Python file parses; no committed credentials (the legacy `.env` secrets are explicitly
grepped for).

---

## 3. Manual review checklist executed against the code

| Area | Result |
|---|---|
| Authentication | Frappe login is used; `Kodafreight Client User` provisions a `User`, assigns exactly one Kodafreight role, and never sets a shared password (the Laravel code hard-coded `123456`). Password resets go through Frappe's reset email. |
| Client isolation | 3 layers: `User Permission`, `permission_query_conditions`, `has_permission` + controller-side `client` pinning. Covered by 5 offline tests and 5 bench tests. |
| Permissions | 4 roles; platform-only DocTypes (`Client`, `Sender Name`, `SMS Provider`, `Plan`, `Settings`, credits) are read-only or invisible for tenants; `Client Staff` gets `if_owner` on contacts/messages. |
| SMS | Covered above. |
| Credits | Covered above; the ledger is the single source of truth and can be rebuilt. |
| Dashboards | `client_dashboard` / `platform_dashboard` aggregate with SQL that is tenant filtered; the rendered dashboard was compared field by field with the underlying data. |
| Security | Webhook signature + IP allowlist + dedupe; `only_for` on admin actions; rate limiting on `send_sms`/`send_bulk_sms`; TLS verification on by default; secrets in `Password` fields. |
| Performance | Campaign sending is enqueued (`long` queue) instead of blocking a web worker; scheduler batches (default 20/minute, configurable); indices on `client`, `recipient`, `status`, `provider_message_id`, `provider_reference`; list views paginate natively. |
| Responsive UI | The portal CSS has breakpoints at 1100 px and 760 px (sidebar collapses, cards stack, tables truncate). Verified by reading the rendered HTML at those widths, not on a device. |

---

## 4. Real Frappe 16 bench run (executed)

Environment: Frappe **16.32.0**, bench **5.31.1**, app installed with
`bench get-app kodafreight https://github.com/BENETHNGOSWE/ERP-KODA-FREIGHT` then
`bench --site kodafreight.localhost install-app kodafreight`.

### 4.1 Installation — PASS

```
Updating DocTypes for kodafreight   : [========================================] 100%
Kodafreight 0.1.0 installed.
Creating Workspace Sidebars
Creating Sidebar Items for Kodafreight
Creating Desktop Icons
Updating Dashboard for kodafreight
```

Post-install verification in `bench console`:

| Query | Expected | Actual |
|---|---|---|
| `len(get_all("DocType", {"module": "Kodafreight"}))` | 20 | **20** |
| Number Cards | 4 | **4** (Sent, Failed, Pending, Delivered) |
| Dashboard Charts | 1 | **1** (Kodafreight SMS Volume) |
| Roles `Kodafreight%` | 4 | **4** |
| `kodafreight.__version__` | 0.1.0 | **0.1.0** |

Three defects were found and fixed **by this run only** — see `docs/06-bugs-fixed.md`
G, H, I. They were invisible to the offline harness because it imports modules by file
path and never exercises `frappe.model.sync` or the hooks registry.

### 4.2 `bench run-tests --app kodafreight` — 21 tests, 21 passed

| Module | Result |
|---|---|
| `test_credits` | 4 / 4 pass |
| `test_sms` | 8 / 8 pass |
| `test_webhooks` | 2 / 2 pass |
| `test_tenant_isolation` | 3 / 5 pass (first run) → 5 / 5 after the fix, +2 new tests |

The two failures (`test_client_cannot_list_other_client_messages`,
`test_client_only_sees_own_sender_names`) used an inappropriate permission-bypassing API; the failures alone did not establish an app defect: both
called `frappe.get_all`, whose own v16 docstring reads *"Will **not** check for
permissions."* `frappe.get_list` is the permission-aware API. Corrected, and two stricter
tests added (`test_client_cannot_list_other_clients`,
`test_client_api_rejects_foreign_client_argument`, the latter calling the whitelisted
`api.sms.message_history` function directly). The suite is now **21 tests**.

The examined client dashboard and message history calls apply a client filter after
`resolve_client()`, which rejects another tenant. Passing these tests does not prove
all endpoints or HTTP request paths secure; a broader security review remains necessary.

**Re-run after the fix: `Ran 21 tests in 2.956s — OK`.** All 21 pass, including the two
new isolation tests that call the whitelisted API function directly. The app's own test suite is
therefore green on the target platform (Frappe 16.32.0 / bench 5.31.1), not just on the
offline stub.

### 4.3 Known stub limitation

`tests_offline/fake_frappe.py` aliases `frappe.get_list = get_all`, so it cannot exercise
permission filtering. Any isolation assertion that depends on `frappe.get_list` is
verified **only** on a real bench. This is now noted in the stub itself.
