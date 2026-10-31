# Kodafreight 0.1.0

A multi-tenant **SMS SaaS platform** for **Frappe Framework / ERPNext v16**, reimplementing
(and hardening) the SMS subsystem of an existing Laravel 10 freight-management SaaS.

* Platform administrator manages clients, sender names, SMS numbers, providers, plans and credits.
* Every client is an isolated tenant with its own users, contacts, sender names, credits,
  messages, campaigns, reports and dashboard.
* SMS is delivered through **Kilakona** (the provider used by the legacy system), with a second
  driver for the older `messaging-service.co.tz` broker.

---

## Installation

```bash
# on your bench (Frappe v16)
cd ~/frappe-bench
bench get-app kodafreight /path/to/kodafreight            # or a git URL
bench --site <site-name> install-app kodafreight
bench --site <site-name> migrate
bench --site <site-name> clear-cache
bench build --app kodafreight
bench restart
```

Requirements: Frappe Framework v16 (ERPNext v16 optional — Kodafreight does not depend on it),
Python ≥ 3.10, MariaDB 10.6+, Redis, a running scheduler (`bench enable-scheduler`) and at
least one background worker.

### First-run configuration

1. **Kodafreight Settings** → set the default country code (255), batch size, retries and the
   webhook secret (generated automatically on install).
2. **Kodafreight SMS Provider → Kilakona** → enter `api_key` / `api_secret`
   (the legacy `SMS_API_KEY2` / `SMS_API_SECRET2` values). Use *Test Connection*.
3. **Kodafreight Sender Name** → create the sender IDs you own (≤ 11 characters).
4. **Kodafreight Client** → create a client, assign sender names in the *Assigned Sender Names*
   table, mark one as default, then use **Credits → Allocate Credits**.
5. **Kodafreight Client User** → create the client administrator; a Frappe user, the matching
   role and a `User Permission` (tenant lock) are created automatically. Use
   *Send Password Reset* to let them in.

Client users land on the **Kodafreight** workspace and can open their portal dashboard at
`/kodafreight`.

---

## Roles

| Role | Can do |
|---|---|
| Kodafreight Platform Admin | everything across all tenants |
| Kodafreight Client Admin | own tenant: users, contacts, sending, campaigns, reports |
| Kodafreight SMS Manager | own tenant: contacts, sending, campaigns, history |
| Kodafreight Client Staff | own tenant, own contacts/messages only |

## DocTypes (20)

`Kodafreight Client`, `Kodafreight Client Sender`*, `Kodafreight Client Setting`*,
`Kodafreight Client Type`, `Kodafreight Client User`, `Kodafreight Plan`,
`Kodafreight Subscription`, `Kodafreight Sender Name`, `Kodafreight SMS Number`,
`Kodafreight SMS Provider`, `Kodafreight SMS Message`, `Kodafreight SMS Campaign`,
`Kodafreight Campaign Recipient`*, `Kodafreight SMS Delivery Log`,
`Kodafreight SMS Credit Account`, `Kodafreight SMS Credit Transaction`,
`Kodafreight Contact`, `Kodafreight Contact Category`, `Kodafreight Audit Log`,
`Kodafreight Settings` (Single).  (* = child table)

## API

| Method | Purpose |
|---|---|
| `kodafreight.api.sms.send_sms` | send one SMS |
| `kodafreight.api.sms.send_bulk_sms` | create + queue a campaign |
| `kodafreight.api.sms.preview_message` | characters / parts / encoding |
| `kodafreight.api.sms.validate_number` | MSISDN validation |
| `kodafreight.api.sms.my_senders`, `my_balance`, `message_history`, `retry_failed` | self-service |
| `kodafreight.api.dashboard.client_dashboard`, `platform_dashboard`, `usage_trend` | dashboards |
| `kodafreight.api.webhooks.delivery_report` | provider delivery reports (signed) |
| `kodafreight.api.webhooks.inbound_message` | inbound (MO) messages (signed) |

Example:

```bash
curl -X POST https://your-site/api/method/kodafreight.api.sms.send_sms \
  -H "Authorization: token <api_key>:<api_secret>" \
  -d recipient=0745788788 -d message="Habari, mzigo wako umefika."
```

## Scheduled jobs

| Schedule | Job |
|---|---|
| every minute | `process_pending_messages` (batch = `queue_batch_size`, default 20) |
| hourly | `expire_subscriptions`, `notify_low_balances` |
| daily | `expire_credits`, `cleanup_delivery_logs` |

## Documentation

| File | Content |
|---|---|
| `docs/01-laravel-system-inspection.md` | full inspection + functional inventory of the Laravel app |
| `docs/02-architecture-and-mapping.md` | Laravel → Frappe mapping, DocTypes, table→DocType migration map |
| `docs/03-sms-provider-integration.md` | Kilakona contract, drivers, webhooks, retries |
| `docs/04-installation-and-migration.md` | installation, upgrade and data migration guide |
| `docs/05-testing-report.md` | what was tested, how, and what was **not** executed |
| `docs/06-bugs-fixed.md` | 24 Laravel defects fixed + 6 found during Kodafreight's own bug hunt |

## Tests

```bash
bench --site <site> run-tests --app kodafreight     # Frappe integration tests
python3 tests_offline/run_tests.py                  # 47 behavioural tests, no bench needed
python3 tests_offline/validate_app.py               # static package validation
```

## Licence

MIT — see `license.txt`.
