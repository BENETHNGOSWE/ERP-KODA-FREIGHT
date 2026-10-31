# Phase 1–3 — Inspection of the existing Laravel system

Source inspected: `FREIGHTMANAGEMENTSYSTEM.zip` (uploaded), extracted to `/home/user/laravel/FREIGHTMANAGEMENTSYSTEM`.
Everything below was read from the actual source code (composer.json, migrations, models, controllers,
routes, middleware, helpers, seeders, blades, console kernel, `.env`, `sms_log.txt`). Nothing is assumed.

---

## 1. Application architecture (Phase 1)

| Item | Finding |
|---|---|
| Framework | Laravel `^10.10` (`composer.json`) |
| PHP | `^8.1` |
| App name in `.env` | `Beneth`, `APP_ENV=local`, `APP_DEBUG=true` |
| Database | **PostgreSQL** (`DB_CONNECTION=pgsql`, DB `freight`) |
| Auth | `laravel/ui` scaffolding (`Auth::routes()`), session guard `web`; `laravel/sanctum ^3.3` for API tokens |
| Authorization | `spatie/laravel-permission ^6.9` (roles + permissions), plus 33 Laravel Policies in `app/Policies` |
| Frontend | Blade + Bootstrap 5 admin template, jQuery, Livewire `^2.5`, Vite |
| Reporting/exports | `maatwebsite/excel`, `barryvdh/laravel-dompdf`, `barryvdh/laravel-snappy` |
| Images | `intervention/image 2.7` (`upload_image()` helper) |
| Queue | `QUEUE_CONNECTION=sync` (jobs run inline); `jobs` + `failed_jobs` tables exist |
| Cache/session | file driver |
| Registration | disabled — `Route::redirect('/register', '/login', 301)` |

### Structure

* **Controllers** are grouped per role/area:
  `Owner/` (platform owner), `Admin/` (client admin), `KodaSMS/`, `Sales/`, `Cashier/`, `Reception/`,
  `Manager/`, `Chiefoperation/`, `Godownmanager/`, `Api/`, `Auth/`.
* **Route files** (registered by `RouteServiceProvider`): `web.php`, `owner.php`, `admin.php`,
  `multiple.php` (the biggest — shared authenticated routes), `api.php`, plus one per role
  (`manager`, `salesperson`, `salesmanager`, `cashier`, `reception`, `godownmanager`, `chiefoperation`).
* **Middleware** (`app/Http/Middleware`): `Owner`, `Admin`, `Manager`, `Cashier`, `ChiefOperation`,
  `Godownmanager`, `Receptionist`, `SalesManager`, `Salesperson`, `Multiple`, `setFreightid`.
  `Multiple::handle()` only checks `auth()->check()` and an optional permission name —
  **it does not enforce tenant scope**; scoping is done manually in every controller constructor:

  ```php
  $this->fms_id = Auth::user()->client_id;      // tenant id taken from the logged-in user
  Model::where('client_id', $this->fms_id)      // repeated in ~every query
  ```

* **Helpers** (globally autoloaded via composer `files`): `app/Helpers/ControllerHelper.php` (587 lines,
  contains the **SMS sending functions**), `app/Helpers/LoadPackageHelper.php`.
* **Jobs**: `app/Jobs/SendPendingSmsJob.php` exists but its `handle()` is **empty (dead code)**.
* **Commands**: `messages:send-pending` (`SendPendingMessagesCommand`).
* **Scheduler** (`app/Console/Kernel.php`): `$schedule->command('messages:send-pending')->everyMinute();`
* **Events / Notifications / Repositories**: none implemented (`EventServiceProvider` has empty `$listen`,
  no `app/Notifications`, no repository layer).
* **API**: thin — `POST /api/login`, `demorequest` endpoints (rate limited `throttle:60,1`),
  `auth:sanctum` group with `user-roles` and `customer-categories`. **No SMS API endpoint and no
  delivery-report webhook exists.**

### Environment variables that matter

```
SMS_API_KEY=beneth            SMS_SECRET_KEY=****           # legacy (messaging-service.co.tz)
SMS_API_KEY2=developerbeneth  SMS_API_SECRET2=****          # ACTIVE (kilakona)
```

---

## 2. SaaS / multi-tenant structure (Phase 2)

The product is a **Freight Management System** sold as SaaS, with a **KodaSMS** bulk-SMS module
(`kodasmspackage` role / package). The SMS subsystem is what this migration targets.

### Tenancy model

* Single shared PostgreSQL database, **no schema separation**.
* Tenant = row in **`clients`**. Tenant key = **`users.client_id`** (nullable bigint).
* The platform owner is a user with role `owner` and `user_type = owner` (seeded `owner@fms.com`),
  usually with `client_id = null`.
* Isolation is *application-level only*: every controller manually filters `where('client_id', $this->fms_id)`.
  There is **no global Eloquent scope, no tenant middleware, no DB-level RLS** → any query that forgets
  the filter leaks data (several were found, see §6 "risks").
* `Owner\ClientController::impersonate()` lets the owner log in as a client admin
  (`session('impersonator_id')`).

### Client creation flow (`Owner\ClientController@store`, transactional)

1. Validate client fields + first admin user fields (`firstname, lastname, email unique:users, password confirmed, phone`).
2. Insert `clients` row.
3. Insert `users` row (`client_id` = new client, `user_type='user'`, `status='1'`, hashed password) and
   `assignRole('admin')`.
4. Insert `client_admins` row (profile of the client's admin, stores plaintext-ish `password` column too).
5. Commit, then upload/resize logo (`upload_image($image,$filename,200)`), default `client_default.png`.

**Deficiency found:** the `clients` table has **no status / active / suspended column**. "Activate/deactivate
a client" is not implemented in the DB; the only lever is deleting the client (`destroy()` deletes the
client admin + its user) or `users.status` (a column that the migration does not create but the code sets).

### Roles (from `PermissionsSeeder`)

`owner` (super-admin, all permissions, `is_super_admin=true`), `admin` (client administrator — all
permissions **except** `clients`, `client_admins`, `sendernames`, `kodapackages`, `customermessages`,
`demorequests`), `manager`, `chiefoperation`, `godownmanager`, `salesperson`, `SalesManager`,
`receptionist`, `Cashier`, **`kodasmspackage`** (SMS-only role: view `client_sendername`, full CRUD on
`customercategories`, `customerleads`, `messages`).

Permissions are `"<action> <model>"` strings for actions `list, view, create, update, delete`.
Later migrations added `roles.client_id` (per-tenant custom roles) and `permissions.scope` (default `client`).
`Admin\StaffController` validates that a chosen role is either global (`client_id IS NULL`) or belongs to
the current tenant — the one place tenant-aware RBAC is enforced.

---

## 3. Functional inventory (Phase 3)

Legend for access: **O**=owner, **A**=client admin, **S**=SMS manager (`kodasmspackage`), **U**=other staff.

### A. System administration
| Feature | Access | Tables | Notes |
|---|---|---|---|
| Owner dashboard | O | – | `dashboards.owner.dashboard`, mostly static |
| Client types | O | `client_types` | seeded |
| Countries | O | `countries` | seeded |
| Packages / Kodapackages (plans) | O | `packages`, `kodapackages`, `module_package`, `modules` | monthly/yearly fee, per-module |
| Subscriptions + invoices + tokens | O | `subscriptions`, `subscription_invoices`, `subscription_tokens` | `generateSubscriptionToken(30)`, `check_if_client_has_subscription()` compares `expire_date >= today` |
| Demo requests (public lead capture) | O + public API | `demorequests` | `POST /api/demorequest/storedemorequests`, throttled |
| Roles & permissions UI | O | spatie tables | `PermissionController`, `RoleController` |
| Impersonate a client | O | – | session flag |

### B. Client management
Create / edit / show / delete client (`clients`, cascades to `client_admins` + `users`), assign logo,
assign package/subscription, assign sender names, assign message quota. **No activate/suspend flag exists.**
Validation: `client_name` unique, `client_email` unique index, `client_type_id` must exist, logo ≤2 MB image.

### C. Staff / user management
`Admin\StaffController` (tenant-scoped): list, create, edit, show, delete (`staffs` soft-deletes), plus
`viewProfile` / `updateProfile`. Creating a staff member creates a `users` row with `client_id` = current
tenant and **hard-coded password `123456`** (`Hash::make('123456')` — the submitted password is ignored),
generates `staff_no` via `createStaffidNumber()` (`{CLIENT}-{n}` sequence), and `syncRoles($role->name)`
after checking the role belongs to the tenant or is global.
Fields: names, gender enum(male/female/others), email, phone, address, photo, document, bio, branch, staff type.

### D. Roles & permissions
Spatie roles/permissions; `roles.client_id` for tenant-local roles; `roles.is_super_admin`,
`roles.is_system_role`; `permissions.scope`. Policies exist per model but most controllers **comment out**
`$this->authorize(...)` calls (e.g. `AssignnameController`, `CustomermessageController` create/store).

### E. SMS management (core of the migration)
| Feature | Access | Route | Behaviour |
|---|---|---|---|
| Message list (own tenant) | A/S | `admin.message.index` | `Message::where('client_id',$fms_id)` |
| Send to customers (no sender choice) | A | `admin.sendmessage` | loops customers, calls `sendMessage([$phone],$text)` — **called with 2 args although the helper needs 3 → `$senderId` is missing (bug)**; writes a `messages` row with status `delivered`/`failed` |
| Send to existing customers (with sender) | A | `admin.sendmessage.multiple` | `sendMessageToExistingCustomer([$phone],$text,$senderId)`, **synchronous, one HTTP request per recipient**; status `delivered`/`failed` |
| Send to leads (queued) | A/S | `admin.sendmessage.lead` | validates `sender_id` (`required|string|max:11`) and `message`; inserts one `messages` row per lead with status **`pending`**; the cron picks them up |
| Cron delivery | system | `messages:send-pending` every minute | takes **20** pending rows, calls `sendMessageLeadPending()`, sets status `delivered`/`failed` |
| Personalisation | A/S | – | `str_replace('{{ customer_firstname }}', ...)` then `strip_tags()` |
| Recipient selection | A/S | – | "send to all" or checkbox list of phone numbers; non-admin staff are limited to leads where `user_id = auth()->id()` |
| Quota gate | A/S | – | `canSendMessages = Message::count(client) < customermessages.messageamount`, computed **only for the view** (`createmultiple`, `createleads`) — **not re-checked on submit** |

`messages` table: `id, customer_number, content, status enum(pending|delivered|failed) default pending,
sender_id (nullable string), client_id, timestamps`. There is **no provider message id, no cost, no parts,
no delivered_at, no error text**, and status `delivered` really means *accepted by the API*.

### F. Sender name management
`sendernames(id, name)` — global pool created by the owner.
`client_sendername(client_id, sendername_id, is_default)` — many-to-many assignment
(`Owner\AssignnameController@store` → `syncWithoutDetaching`, returns JSON).
`clientactivenames(client_id, sendername_id)` — the client's currently *active* sender name
(`Admin\ActivesendernameController`). Clients only see `Sendername::whereHas('clients', ... client.id = $fms_id)`.
**Not enforced at send time:** the submitted `sender_id` string is passed straight to the provider without
verifying it is assigned to the tenant → a client can spoof any sender name (security bug).

### G. Phone number management
**Does not exist** in the Laravel system. No `numbers` table, no controller. (Kodafreight adds it as a
clearly-marked new module — see mapping doc.)

### H. Message credits / limits
`customermessages(client_id, messageamount string)` — a flat allocation per client set by the owner
(create/edit/delete under `owner.*.customermessage`). Usage = `count(messages)` for the client.
**There is no ledger, no decrement, no refund, no expiry, no per-part cost**, and the limit is only used to
show/hide the send form. Multi-part messages count as 1.

### I. SMS history
`messages` list per tenant (`dashboards.admin.messages.index`, `indexmultiple`, `kodasms/messages/indexlead`).
No pagination, no filters, no export, no delivery-report reconciliation.

### J. Reports
Sales reports (`sales_reports`, Excel export), office expenses report (+PDF), financial report
(chiefoperation), invoices (PDF via dompdf/snappy). **No SMS report.**

### K. Dashboards
* `HomeController@index` → `dashboards.dynamic_dashboard` with tenant counts.
* `KodaSMS\SMSManagerDashboardController@index` → cards: **total leads, total categories,
  `messages delivered / messageamount` with a progress bar, failed messages**.
  (Note: the class calls `Auth::check()` without importing the facade and registers middleware `'mutliple'`
  — a typo for `multiple` → this controller is broken at runtime; the dynamic dashboard is used instead.)
* Sidebar (`dashboards/kodasms/sidebar.blade.php`): Dashboard, Message → Chat With Customer / Message List,
  Leads → Categories List / Leads List, Manage Profile → Your Profile, Settings → Sender Name.

### L. Notifications
Flash messages only (`with('success'|'error')`). Mail is configured (mailpit) but nothing sends mail.
No SMS notification templates.

### M. API integrations
**SMS provider = Kilakona** (see Phase 9 doc). Legacy commented-out code targets
`https://messaging-service.co.tz/api/sms/v1/text/single`, and `sms_log.txt` shows an older
`{"auth":{"clientId","clientSecret"},"messages":[{text,msisdn,source,reference,coding:"GSM7"}]}`
payload returning `{status:1, message:"Successfully submitted 1 message(s) to broker.", balance:10214}`.

### N. Settings
`system_settings(client_id, key, value, updated_by)` — generic per-tenant key/value store (added 2025-11).
Global settings live in `.env` / `config/*`, including `config/sidebarmenu.php`.

### O. Billing / subscription
`packages`, `kodapackages` (monthly_fee, yearly_fee), `subscriptions` (+duration, expire_date),
`subscription_invoices`, `subscription_tokens`, `client_payments`, `payments`, `payment_types`, `banks`.
Enforcement helper `check_if_client_has_subscription()` exists but is not wired into the SMS flow.

### P. Security
CSRF on web forms, bcrypt hashes, Sanctum for API, `throttle:60,1` on the demo endpoint.
Weaknesses found: `APP_DEBUG=true` with committed secrets in `.env`; `curl` calls use
`CURLOPT_SSL_VERIFYPEER = false`; `client_admins.password` stores the raw password; staff password is
hard-coded `123456`; `authorize()` calls commented out; sender-name ownership not verified at send time;
no rate limiting on the send endpoints; no webhook signature (no webhook at all).

### Q. Audit logs
**None.** Only `\Log::error()` on exceptions and `sms_log.txt` (a manual dump of one request/response).

### R. Other functionality discovered
Freight domain (out of scope for the SMS SaaS but noted): customers, customer categories, customer leads
(+Excel import/template), load packages & types, containers & container loadings, delivery orders, godowns,
branches, invoices & invoice loads (+exchange rate, CBM price), payments, office expenses & categories,
company invoice details, unit types, sales reports, banks, patients (leftover from a hospital template).

---

## 4. Per-feature detail for the SMS subsystem (Phase 3 required fields)

### Send SMS to leads (the canonical, still-maintained flow)
1. **What it does** – queues a personalised SMS for each selected lead of the tenant.
2. **Who** – roles `admin`, `kodasmspackage` (all leads of the tenant); other staff only their own leads
   (`customerleads.user_id = auth()->id()`).
3. **Creates** – N rows in `messages` (`status='pending'`, `sender_id`, `client_id`).
4. **Reads** – `customerleads`, `sendernames` ⋈ `client_sendername`, `customermessages`, `messages`.
5. **Tables** – `messages`, `customerleads`, `sendernames`, `client_sendername`, `customermessages`.
6. **On action** – redirect back with "N messages queued for sending".
7. **Validations** – `sender_id: required|string|max:11`, `message: required|string`,
   non-empty recipient set; HTML stripped.
8. **Permissions** – `create messages` (role-level only; ownership of the sender name is *not* checked).
9. **External API** – none at submit time; the cron calls Kilakona later.
10. **Success** – rows become `delivered` when the API returns `success: true`.
11. **Failure** – rows become `failed`; the API error text is **discarded**; no retry; credits unaffected
    (there is no credit ledger).

### Immediate send to existing customers
Same, but the provider is called **inside the request loop** (1 HTTP call per recipient, no timeout set,
`SSL_VERIFYPEER=false`), so a 500-recipient blast blocks the web worker; partial failures are reported by
listing failed numbers.

---

## 5. Database inventory (56 tables from `database/migrations`)

Tenant-scoped tables carry a `client_id`:
`users, staffs, branches, customers, customercategories, customerleads, customerlead_category, messages,
customermessages, client_sendername, clientactivenames, containers, containerloadings, loadpackages,
loadpackagetypes, unit_types, invoices, invoice_loads, payments, client_payments, office_expenses,
office_expense_categories, companyinvoicedetails, derivery_orders, godown_addresses, sales_reports,
subscriptions, subscription_invoices, subscription_tokens, modules, packages, kodapackages, system_settings,
roles(client_id nullable)`.

Global tables: `clients, client_types, client_admins, countries, banks, sendernames, staff_types,
payment_types, demorequests, permissions, model_has_roles, model_has_permissions, role_has_permissions,
jobs, failed_jobs, personal_access_tokens, password_reset_tokens, migrations, patients`.

---

## 6. Bugs / risks found in the Laravel code (carried into the redesign as fixes)

1. `Admin\MessageController@sendMessage` calls `sendMessage($phones,$text)` — the helper signature is
   `($phoneNumbers,$message,$senderId)` → **PHP ArgumentCountError / missing sender**.
2. Quota (`customermessages.messageamount`) is **never enforced on submit**, only in the view.
3. Usage is `count(messages)` — includes failed and pending messages, ignores multi-part billing.
4. Sender name is **not validated against the tenant's assignments** at send time.
5. `SMSManagerDashboardController` uses `Auth::` without importing it and middleware `'mutliple'` (typo).
6. `SendPendingSmsJob::handle()` is empty; the real work is in a cron command with a fixed batch of 20 and
   no locking → **two overlapping runs send the same message twice**.
7. No delivery-report/callback handling: `delivered` only means "accepted by the broker".
8. `CURLOPT_SSL_VERIFYPEER=false` and no `CURLOPT_TIMEOUT`.
9. Secrets committed in `.env`; `client_admins.password` stores the password in clear.
10. Staff passwords hard-coded to `123456`.
11. `clients` has no status column → "deactivate client" is impossible.
12. Policies exist but many `authorize()` calls are commented out.
13. `Customermessage` allows multiple quota rows per client; `->value('messageamount')` silently uses the
    first one.
14. No phone-number normalisation (Kilakona expects MSISDN `2557XXXXXXXX`).
