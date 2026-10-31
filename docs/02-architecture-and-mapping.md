# Phase 4–7 & 11 — Laravel → Kodafreight (Frappe/ERPNext v16) mapping & architecture

## 1. Concept mapping

| Laravel concept | Kodafreight / Frappe v16 implementation |
|---|---|
| Eloquent Model | DocType (`kodafreight/kodafreight/doctype/...`) |
| Controller action | DocType controller method / whitelisted API in `kodafreight/api/` |
| `Multiple` / role middleware | Role Permissions + `permission_query_conditions` + `has_permission` hooks (`kodafreight/permissions.py`) |
| Manual `where('client_id', …)` | **User Permission** on `Kodafreight Client` + server-side query conditions (defence in depth) |
| Spatie roles | Frappe Roles (`Kodafreight Platform Admin`, `Kodafreight Client Admin`, `Kodafreight SMS Manager`, `Kodafreight Client Staff`) |
| Spatie permissions | Role Permission rules per DocType (read/write/create/delete/submit) + `if_owner` for staff |
| Policies | `has_permission` hook + `validate()` in controllers |
| `messages:send-pending` cron | `scheduler_events.cron` → `process_pending_messages` (batch, **row-locked**) |
| `SendPendingSmsJob` (empty) | `frappe.enqueue` background jobs for campaigns (`short`/`long` queues) |
| Blade dashboards | Desk **Workspace** (number cards + charts) and a **portal page** `/kodafreight` for client users |
| `.env` SMS keys | `Kodafreight SMS Provider` DocType with `Password` fieldtype (encrypted at rest) |
| `system_settings` (per client k/v) | `Kodafreight Client Setting` child table on the client + `Kodafreight Settings` (Single) for global |
| Flash messages | `frappe.msgprint` / `frappe.throw` |
| Excel/PDF exports | Native Frappe report export |
| Sanctum API | Frappe REST API + API key/secret, `@frappe.whitelist()` |

## 2. Table → DocType mapping (Phase 11)

| Laravel table | Kodafreight DocType | Field mapping / transformation |
|---|---|---|
| `clients` | **Kodafreight Client** | `client_name`→`client_name`, `client_email`→`email`, `client_phonenumber`→`phone`, `client_location/city/country/address_one/address_two/zipcode/fax/website_link/tinnumber`→same-named fields, `client_logo`→`logo` (File), `client_type_id`→`client_type` (Link). **New:** `status` (Active/Inactive/Suspended) — fixes the missing activate/deactivate capability. `name` = `CLI-#####` series, `client_code` kept for legacy id. |
| `client_admins` + `users` | **Kodafreight Client User** (+ core `User`) | `firstname/middlename/lastname`→`first_name/middle_name/last_name`, `email`→`email` (also the `User` name), `phone`, `client_id`→`client`, role→`user_role` (Client Admin / SMS Manager / Client Staff). The plaintext `password` column is **not** migrated (security). A `User Permission` row is created for tenant isolation. |
| `staffs` | **Kodafreight Client User** | `staff_no`→`staff_no`, `staff_gender`→`gender`, `staff_address`→`address`, `staff_bio`→`bio`, `branch_id`→`branch` (kept as Data in v0.1), soft-delete→`enabled=0`. |
| `sendernames` | **Kodafreight Sender Name** | `name`→`sender_name` (unique, ≤11 chars), **new:** `status`, `provider`, `description`. |
| `client_sendername` | **Kodafreight Client Sender** (child of Client) | `is_default`→`is_default`; **new:** `enabled`. |
| `clientactivenames` | same child row with `is_default = 1` | the "active name" is now the default sender of the client (one per client, validated). |
| `customermessages` | **Kodafreight SMS Credit Account** (1 per client) + **Kodafreight SMS Credit Transaction** | `messageamount` (string) → `allocated` (Int) via an initial `Allocation` transaction. Used/balance are derived from the ledger instead of `count(messages)`. |
| `messages` | **Kodafreight SMS Message** | `customer_number`→`recipient` (normalised MSISDN), `content`→`message`, `status` (`pending|delivered|failed`) → `status` (`Queued|Sending|Sent|Delivered|Failed|Cancelled`; legacy `pending`→`Queued`, `delivered`→`Sent`), `sender_id`→`sender_name`, `client_id`→`client`. **New:** `parts`, `encoding`, `credits_used`, `provider`, `provider_reference`, `provider_message_id`, `error_message`, `sent_at`, `delivered_at`, `retry_count`, `campaign`, `contact`. |
| `customers`, `customerleads` | **Kodafreight Contact** | merged; `source` = `Customer` / `Lead`; `customer_firstname/lastname`, `phone`, `customer_email`, `category_id`→`category`, `user_id`→`assigned_to`. |
| `customercategories` | **Kodafreight Contact Category** | `categoryname`→`category_name`, `categorydescription`→`description`, `client_id`→`client`. |
| `system_settings` | **Kodafreight Client Setting** (child of Client) | `key`/`value` pairs preserved verbatim. |
| `client_types` | **Kodafreight Client Type** | `name`. |
| `kodapackages`, `packages`, `subscriptions`, `subscription_invoices/tokens`, `client_payments` | **Kodafreight Plan** + **Kodafreight Subscription** | v0.1 implements plan (monthly/yearly fee, included SMS credits, validity) and subscription (start/end, status, plan, auto credit top-up). Invoicing/payments are intentionally delegated to **ERPNext Sales Invoice** (native, better) — documented as an improvement, not a removal: the freight/finance tables are out of scope for the SMS SaaS. |
| freight tables (`loadpackages`, `containers`, `invoices`, …) | **not migrated** | out of scope for the SMS platform; ERPNext core (Stock/Selling/Accounts) covers them natively. Explicitly documented so nothing is silently dropped. |
| *(no Laravel table)* | **Kodafreight SMS Number** | new module requested by the brief; the Laravel app has no number management. |
| *(no Laravel table)* | **Kodafreight SMS Delivery Log**, **Kodafreight Audit Log**, **Kodafreight SMS Campaign** (+ recipients child) | new: delivery callbacks, audit trail, bulk campaigns replacing the ad-hoc loop. |

### Migration procedure (documented, optional for v0.1)
`kodafreight/utils/migrate_from_laravel.py` provides `import_from_json(path)` which consumes a JSON dump
produced by `pg_dump`-to-JSON (or `php artisan tinker` export) with keys
`clients, client_admins, staffs, sendernames, client_sendername, clientactivenames, customermessages,
customerleads, customers, customercategories, messages` and creates the corresponding documents in
dependency order, remapping ids → Frappe names, normalising phone numbers and status enums, and creating
one opening `Allocation` credit transaction per client. It is idempotent (skips existing records by
`legacy_id`). Every tenant DocType carries a `legacy_id` (Data, hidden, indexed) so re-runs are safe.

## 3. DocType list (Phase 7)

Platform: `Kodafreight Settings` (Single), `Kodafreight SMS Provider`, `Kodafreight Sender Name`,
`Kodafreight SMS Number`, `Kodafreight Client Type`, `Kodafreight Plan`, `Kodafreight Client`,
`Kodafreight Client Sender` (child), `Kodafreight Client Setting` (child), `Kodafreight Subscription`,
`Kodafreight Client User`, `Kodafreight Audit Log`.

SMS: `Kodafreight SMS Message`, `Kodafreight SMS Campaign`, `Kodafreight Campaign Recipient` (child),
`Kodafreight SMS Delivery Log`, `Kodafreight SMS Credit Account`, `Kodafreight SMS Credit Transaction`,
`Kodafreight Contact`, `Kodafreight Contact Category`.

## 4. Roles & permission matrix

| Role | Scope | Highlights |
|---|---|---|
| **Kodafreight Platform Admin** | all tenants | full CRUD on clients, providers, sender names, numbers, plans, credits (allocate), all messages, settings, audit log |
| **Kodafreight Client Admin** | own tenant only | read own client, manage own client users, contacts/categories, send SMS, campaigns, read own messages/credits/senders; **cannot** create clients, sender names, providers, credits |
| **Kodafreight SMS Manager** | own tenant only | contacts, categories, send SMS, campaigns, messages, read credits/senders (mirrors `kodasmspackage`) |
| **Kodafreight Client Staff** | own tenant, `if_owner` on contacts/messages | send SMS to their own contacts only (mirrors the Laravel `user_id = auth()->id()` rule) |

Isolation is enforced by three independent layers:
1. **User Permission** (`Kodafreight Client` = X) auto-created for every `Kodafreight Client User`, applied
   to all tenant DocTypes through their `client` Link field (`apply_to_all_doctypes = 1`).
2. **`permission_query_conditions`** — every list/report query for a tenant DocType gets
   `` `tabX`.client = 'CLI-000x' `` appended server-side.
3. **`has_permission`** — per-document check, and `validate()` in each controller forces
   `self.client = get_user_client()` for non-platform users, so a forged payload cannot write into another
   tenant.

## 5. Credit model (fixes Laravel's counter)

`Kodafreight SMS Credit Account` (one per client, `autoname = field:client`) holds
`allocated`, `used`, `balance`, `low_balance_threshold`, `allow_negative`, `expiry_date`.
Every movement writes a `Kodafreight SMS Credit Transaction`
(`Allocation | Deduction | Refund | Adjustment | Expiry`) with `balance_after`, `reference_doctype/name`.
Deduction happens **at dispatch**, sized by the number of SMS parts
(GSM-7: 160 / 153 per part; UCS-2: 70 / 67), inside `SELECT … FOR UPDATE` (`frappe.db.get_value(..., for_update=True)`)
so concurrent workers cannot oversell. A failed provider call triggers an automatic **Refund**.
Balance is never allowed below zero unless `allow_negative` is explicitly set on the account.

## 6. Application layout (Frappe v16 app structure)

```
kodafreight/                     # repo root (bench get-app target)
├── pyproject.toml               # v15/v16 packaging (flit_core)
├── license.txt  README.md
└── kodafreight/
    ├── __init__.py              # __version__ = "0.1.0"
    ├── hooks.py                 # app meta, fixtures, doc_events, scheduler, permissions, website route
    ├── modules.txt              # "Kodafreight"
    ├── patches.txt
    ├── install.py               # after_install: roles, settings, default provider, workspace defaults
    ├── permissions.py           # tenant isolation (query conditions + has_permission)
    ├── api/                     # whitelisted endpoints (sms.py, dashboard.py, webhooks.py)
    ├── services/
    │   ├── credits.py
    │   └── sms/ (base.py, kilakona.py, messaging_service_tz.py, factory.py, dispatcher.py, encoding.py, phone.py)
    ├── tasks.py                 # scheduled jobs
    ├── utils/                   # audit.py, tenant.py, migrate_from_laravel.py
    ├── kodafreight/             # the module folder
    │   ├── doctype/<20 doctypes>/
    │   ├── workspace/kodafreight/kodafreight.json
    │   ├── number_card/…  dashboard_chart/…
    │   └── report/<3 query reports>/
    ├── public/{css,js}
    ├── templates/
    └── www/kodafreight/         # client portal dashboard (index.html + index.py)
```
