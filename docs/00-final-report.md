# Kodafreight v0.1 — Final report

---

## 1. Understanding of the Laravel system

**What it is.** `FREIGHTMANAGEMENTSYSTEM` is a Laravel 10 / PHP 8.1 freight-forwarding ERP for a
Tanzanian cargo business (Dar es Salaam ⇄ China), which additionally hosts a **multi-tenant SMS
SaaS** ("KodaSMS") for its clients. PostgreSQL, Blade + Bootstrap, Spatie roles/permissions,
no `.sql` dump — the schema was reconstructed from `database/migrations/`.

**Tenancy model.** Two overlapping mechanisms:

* `clients` (the SMS tenants) + `client_admins` + `staffs`, and
* a `Multiple` middleware that reads `Auth::user()->client_id` and pushes it into the session;
  all "owner"/"multiple" routes then filter on that value **in each controller by hand**.

There is no database-level or query-level isolation: any controller that forgets the
`where('client_id', …)` clause leaks another tenant's data, and several do.

**SMS subsystem.** One live send path: `ControllerHelper::sendMessage()` → `POST` to Kilakona
(`messaging.kilakona.co.tz/api/v1/vendor/message/send`) with `api_key`/`api_secret` headers and a
`{senderId, messageType, message, contacts}` body. A `messages` table holds
`pending/delivered/failed`; an artisan command (`sms:send-pending`, every minute) takes 20 pending
rows and sends them. Quotas live in `customermessages.messageamount`; sender IDs in
`sendernames` + `client_sendername` + `clientactivenames`; contacts in `customers`,
`customerleads`, `customercategories`. Roles: `admin` (client admin) and `kodasmspackage`
(SMS manager), plus platform "owner" routes.

**Functional inventory.** `docs/01-laravel-system-inspection.md` answers the 11 standard questions
for every category **A–R** (authentication, tenancy, clients, users/staff, roles & permissions,
sender names, SMS numbers, message credits/limits, single send, bulk send, contacts/leads,
message history, delivery status, dashboards, reports, provider integration, scheduling/queues,
freight/finance modules that are out of scope) with file/line references. The Laravel → Frappe
mapping (table → DocType, controller → controller/API, middleware → permission layer,
blade → desk/portal) is `docs/02-architecture-and-mapping.md`.

**Rule I did not invent anything for:** every business rule in Kodafreight is traceable to a
Laravel construct, or is an explicit hardening documented in `docs/06-bugs-fixed.md`.

---

## 2. Features implemented in Kodafreight

| Area | Delivered |
|---|---|
| Tenancy | `Kodafreight Client` with status (Active/Inactive/Suspended), types, plans, subscriptions, per-client settings; strict isolation enforced in 3 server-side layers |
| Users | `Kodafreight Client User` provisions a Frappe `User`, one Kodafreight role and a `User Permission`; enable/disable; password-reset action (no shared passwords) |
| Roles | Platform Admin, Client Admin, SMS Manager, Client Staff |
| Sender names | Platform-owned `Kodafreight Sender Name` (≤ 11 chars, charset validated) assigned per client with one default; usage enforced on every send |
| SMS numbers | `Kodafreight SMS Number` (short code / long number), assignable to a client, used for inbound routing |
| Credits | `SMS Credit Account` + immutable `SMS Credit Transaction` ledger (Allocation/Deduction/Refund/Adjustment/Expiry) with running balance, no overdraft, rebuild tool, low-balance alerts, optional expiry |
| Sending | Single send, bulk/campaign send with personalisation and de-duplication, drafts, retries, immutable sent history |
| Delivery status | `Queued → Sending → Sent → Delivered/Failed`, signed provider callbacks, `SMS Delivery Log` with duplicate protection, refunds on confirmed failure |
| Contacts | `Kodafreight Contact` (+ categories), MSISDN normalisation, per-client duplicate detection, customer/lead source |
| Dashboards | Desk workspace + 4 number cards + volume chart for the platform admin; branded client portal at `/kodafreight` (credits, usage %, success rate, 7-day trend, recent messages, campaigns, quick actions) |
| Reports | SMS Usage Summary (with chart), SMS Delivery Report, Client Credit Statement — all tenant filtered |
| API | 9 whitelisted SMS/self-service methods + 3 dashboard methods + 2 webhooks, with rate limiting |
| Automation | Per-minute queue processing, hourly subscription/low-balance checks, daily credit expiry and log cleanup |
| Security | Encrypted provider secrets, HMAC-signed webhooks + IP allowlist, `only_for` on admin actions, TLS verification, audit log of every sensitive event |
| Migration | `utils/migrate_from_laravel.py` — idempotent importer with `legacy_id` tracking and dry-run |

Size: **87 Python modules, ~5,900 lines**, 20 DocTypes, 3 reports, 4 number cards, 1 chart,
1 workspace, 1 portal page.

---

## 3. ERPNext/Frappe architecture

```
kodafreight/                       app root (flit build, frappe >=15,<17)
├── kodafreight/
│   ├── hooks.py                   doc_events, scheduler, permission hooks, website route, assets
│   ├── permissions.py             permission_query_conditions + has_permission per DocType
│   ├── events.py / tasks.py       document events, scheduled jobs, background workers
│   ├── install.py                 roles, client types, default provider, settings, webhook secret
│   ├── api/{sms,webhooks,dashboard}.py     whitelisted endpoints
│   ├── services/
│   │   ├── credits.py             the ledger (single source of truth for money)
│   │   └── sms/{base,transport,kilakona,messaging_service_tz,generic_http,factory,
│   │            encoding,phone,dispatcher}.py
│   ├── utils/{tenant,audit,jinja,migrate_from_laravel}.py
│   ├── kodafreight/               module: doctype/ (20), report/ (3), workspace/,
│   │                              number_card/ (4), dashboard_chart/
│   ├── templates/includes/kodafreight_dashboard.html
│   ├── www/kodafreight/           client portal page (index.py + index.html)
│   ├── public/{css,js}
│   └── tests/                     FrappeTestCase suites
├── docs/ (6 documents)  ├── tests_offline/ (harness)  └── README.md
```

**Tenant isolation — server side only, three layers**

1. **`User Permission`** on `Kodafreight Client` per client user — Frappe filters every linked
   DocType automatically.
2. **`permission_query_conditions`** — every tenant DocType gets `… .client = '<client>'`
   appended to list/report/API queries; platform admins bypass it.
3. **`has_permission` + controller pinning** — document-level check, and every controller sets
   `self.client = get_user_client()` on insert, so a forged `client` in a REST payload raises
   `PermissionError`. Whitelisted API methods resolve the tenant from the session, never from the
   request body.

No isolation whatsoever relies on the client script, the workspace or hidden form fields.

---

## 4. SMS integration

Full detail in `docs/03-sms-provider-integration.md`. Summary:

* **Provider kept as-is: Kilakona**, exactly the same URL, headers and JSON body as the Laravel
  helper (asserted byte-for-byte in a test), so existing credentials and sender IDs keep working.
  Drivers for the legacy `messaging-service.co.tz` broker and a Generic HTTP provider are included;
  adding another provider is one file + one registry entry.
* Credentials live in a `Password` field (encrypted at rest, never exported, never in the ZIP).
* Pipeline: client active → sender owned & active → MSISDN normalisation → GSM7/UCS2 segmentation
  → credit deduction under a row lock → HTTP call with timeout and TLS verification → status,
  provider id/reference, raw response, audit entry → refund on failure → backoff retry on
  transport/5xx errors.
* **Delivery reports**: `…/api/method/kodafreight.api.webhooks.delivery_report`, HMAC-SHA256
  signature, optional IP allowlist, provider-specific status mapping, `sha256` dedupe key with a
  unique index so a repeated callback cannot double-refund. Inbound (MO) messages are accepted on
  a second endpoint and routed by destination SMS number.

---

## 5. Testing

Full report: `docs/05-testing-report.md`. Executed in this environment:

```
python3 tests_offline/run_tests.py    →  47 passed, 0 failed, 47 total
python3 tests_offline/validate_app.py →  20 DocTypes, 0 errors, 0 warnings
python3 tests_offline/render_dashboard.py → dashboard rendered from the shipped template
```

The 47 tests import the **real** application modules and run them on an in-memory Frappe stub:
encoding/segmentation (5), phone normalisation (3), provider drivers and status mapping (4),
credit ledger (7), tenant isolation and sender ownership (5), dispatch incl. retries, refunds,
idempotent delivery reports and immutability (12), campaigns (4), client and contact rules (7).

The static validator checks selected issues that can affect migration: JSON/field-order integrity,
Link/Table targets, child-table flags, Select defaults, autoname fields, controller class names,
every dotted path in `hooks.py`, workspace/report/card references, `frm.call` targets, Python
syntax, and committed secrets.

Also shipped: `kodafreight/tests/` — four `FrappeTestCase` suites (credits, SMS dispatch, tenant
isolation, webhooks) for `bench --site <site> run-tests --app kodafreight`.

**Real bench:** the user successfully installed the app on Frappe 16.32.0 and verified 20 DocTypes, four cards, one chart and four roles. After correcting two tests that bypassed permissions, the user reported `Ran 21 tests in 2.956s — OK`. These tests do not establish complete security or live SMS delivery. A real browser smoke test and live provider integration remain unverified. The dashboard visual is not a browser screenshot.

---

## 6. Bugs found and fixed

`docs/06-bugs-fixed.md` lists **24 defects in the Laravel system** with file references and the
test that now covers the fix, plus **11 internal issues documented (A–K)** — six
earlier issues, three (G–I) found on a **real Frappe 16 bench**, and two test/harness limitations (J–K): a missing
module `__init__.py` that aborted `bench install-app`, number cards/dashboard charts that were
never imported because `importable_doctypes` was not declared, and a Workspace with a null `app`
field. All three are fixed and the validator now guards against them.
The most serious ones:

1. `sendMessage()` called with two arguments against a three-argument signature — fatal error.
2. Any tenant could send with **any** sender ID (only a length check existed).
3. Quota checked when rendering the form, never on submit; usage counted with `Message::count()`
   (failed rows included, multi-part messages under-billed).
4. The pending-messages cron had no locking → overlapping runs sent the same SMS twice.
5. `status = delivered` was written on API acceptance; real delivery was never known and there was
   no callback endpoint at all.
6. `CURLOPT_SSL_VERIFYPEER = false` and no timeout.
7. Staff accounts hard-coded the password `123456`; client-admin passwords were stored in
   plaintext in a column.
8. Clients had no status column, so a tenant could not be suspended.
9. `SMSManagerDashboardController` referenced `Auth` without importing it and registered a
   middleware alias with a typo (`'mutliple'`).

Kodafreight's own hunt fixed, among others, a Python operator-precedence bug in role
synchronisation, a mandatory `sender_name` that broke inbound messages, a `rowcount`-dependent
queue claim, and manual credit transactions that could have bypassed the ledger.

---

## 7. Installation

```bash
cd ~/frappe-v16
bench get-app https://github.com/BENETHNGOSWE/ERP-KODA-FREIGHT.git
bench --site kodafreight.localhost install-app kodafreight
bench --site kodafreight.localhost run-tests --app kodafreight
```

Then: enter the Kilakona credentials in **Kodafreight SMS Provider**, create sender names, create a
client, assign senders, allocate credits, create the client administrator. Full guide including the
Laravel data migration: `docs/04-installation-and-migration.md`.

---

## 8. Client dashboard visual

`kodafreight-client-dashboard.html` (also inside the package as
`docs/client-dashboard-render.html`).

**This is not a browser screenshot.** No browser is available in this environment. It is a real
render of the shipped Jinja template `templates/includes/kodafreight_dashboard.html` plus the
shipped stylesheet `public/css/kodafreight_portal.css` and the shipped controller logic in
`www/kodafreight/index.py`, driven by data created through the real send pipeline
(`tests_offline/render_dashboard.py`). What a logged-in client user sees at `/kodafreight` is this
markup, served by Frappe's website renderer.

It shows: tenant header and sender chips, credit balance 8,168 / 10,000 with usage bar, messages
this month, success rate, a 7-day sent/failed bar chart, recent messages with status pills
(Delivered / Sent / Queued / Failed), campaigns, and quick actions.

---

## 9. Final package

**`kodafreight-v0.1.zip`** — 180 KB, 195 files.

```
kodafreight/
  README.md, license.txt (MIT), pyproject.toml, requirements.txt, MANIFEST.in, .gitignore
  docs/  00-final-report.md, 01-laravel-system-inspection.md, 02-architecture-and-mapping.md,
         03-sms-provider-integration.md, 04-installation-and-migration.md,
         05-testing-report.md, 06-bugs-fixed.md, client-dashboard-render.html
  kodafreight/   the installable Frappe app (20 DocTypes, services, API, portal, reports, tests)
  tests_offline/ the 47-test harness + static validator + dashboard renderer
```

Excluded: `.env` and any credential file, `__pycache__`/`*.pyc`, VCS and node artefacts. The
package was grepped for the legacy Laravel secrets and API keys — none are present; provider
credentials are entered at runtime into an encrypted `Password` field.
