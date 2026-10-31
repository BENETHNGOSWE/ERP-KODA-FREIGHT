# Phase 16 — Installation, upgrade and data migration

## 1. Requirements

| Component | Version |
|---|---|
| Frappe Framework | **v16** (the app also loads on v15: `pyproject.toml` declares `>=15,<17`) |
| ERPNext | v16 optional — Kodafreight has **no** ERPNext dependency |
| Python | ≥ 3.10 |
| MariaDB | ≥ 10.6 |
| Redis | queue + cache (bench default) |
| Node | ≥ 18 (for `bench build`) |

## 2. Clean installation

```bash
cd ~/frappe-bench

# option A - from the delivered archive
unzip kodafreight-v0.1.zip -d /tmp
bench get-app kodafreight /tmp/kodafreight

# option B - from git
bench get-app kodafreight https://<your-git-host>/kodafreight.git

bench --site <site-name> install-app kodafreight
bench --site <site-name> migrate
bench --site <site-name> clear-cache
bench build --app kodafreight
bench --site <site-name> enable-scheduler
bench restart
```

`after_install` creates:

* the four roles (`Kodafreight Platform Admin/Client Admin/SMS Manager/Client Staff`),
* the default client types,
* the **Kilakona** provider record (without credentials — you enter them),
* `Kodafreight Settings` with country code 255, batch size 20, retries 3, and a generated
  webhook secret.

### Verification checklist after install

```bash
bench --site <site-name> console
```
```python
frappe.db.count("DocType", {"module": "Kodafreight"})     # -> 20
frappe.db.exists("Role", "Kodafreight Platform Admin")    # -> "Kodafreight Platform Admin"
frappe.db.exists("Workspace", "Kodafreight")              # -> "Kodafreight"
frappe.get_single("Kodafreight Settings").queue_batch_size # -> 20
frappe.db.exists("Kodafreight SMS Provider", "Kilakona")  # -> "Kilakona"
```

Then open `/app/kodafreight` (desk) and `/kodafreight` (client portal).

## 3. Uninstall / reinstall

```bash
bench --site <site-name> uninstall-app kodafreight --yes
bench --site <site-name> install-app kodafreight
```

## 4. Upgrade

```bash
cd apps/kodafreight && git pull && cd ../..
bench --site <site-name> migrate
bench build --app kodafreight
bench restart
```
`after_migrate` re-creates any missing role or settings value; it never overwrites data.

## 5. Data migration from the Laravel application (optional for v0.1)

Kodafreight installs and runs without any legacy data. When you do want to carry the tenants
across, use `kodafreight/utils/migrate_from_laravel.py`.

### Step 1 — export from PostgreSQL

```bash
psql -d freight -At -c "select json_build_object(
  'client_types',       (select coalesce(json_agg(t),'[]') from client_types t),
  'clients',            (select coalesce(json_agg(t),'[]') from clients t),
  'client_admins',      (select coalesce(json_agg(t),'[]') from client_admins t),
  'users',              (select coalesce(json_agg(t),'[]') from users t),
  'staffs',             (select coalesce(json_agg(t),'[]') from staffs t),
  'sendernames',        (select coalesce(json_agg(t),'[]') from sendernames t),
  'client_sendername',  (select coalesce(json_agg(t),'[]') from client_sendername t),
  'clientactivenames',  (select coalesce(json_agg(t),'[]') from clientactivenames t),
  'customermessages',   (select coalesce(json_agg(t),'[]') from customermessages t),
  'customercategories', (select coalesce(json_agg(t),'[]') from customercategories t),
  'customers',          (select coalesce(json_agg(t),'[]') from customers t),
  'customerleads',      (select coalesce(json_agg(t),'[]') from customerleads t),
  'messages',           (select coalesce(json_agg(t),'[]') from messages t))" > laravel.json
```

### Step 2 — dry run, then import

```bash
bench --site <site> execute kodafreight.utils.migrate_from_laravel.run \
  --kwargs "{'path': '/tmp/laravel.json', 'dry_run': True}"

bench --site <site> execute kodafreight.utils.migrate_from_laravel.run \
  --kwargs "{'path': '/tmp/laravel.json', 'dry_run': False}"
```

### What the importer does

| Source | Destination | Transformation |
|---|---|---|
| `client_types` | `Kodafreight Client Type` | name → `type_name` |
| `clients` | `Kodafreight Client` | field-by-field; `status` defaults to **Active** (the legacy table has none) |
| `sendernames` | `Kodafreight Sender Name` | `status = Active` |
| `client_sendername` + `clientactivenames` | `Kodafreight Client Sender` child rows | the "active name" becomes `is_default = 1` |
| `customermessages.messageamount` | one `Allocation` credit transaction | string → int; `≤0` skipped |
| `client_admins` | `Kodafreight Client User` (`Client Admin`) | plaintext `password` column **not** migrated |
| `staffs` | `Kodafreight Client User` | role from the linked `users.user_type`; `deleted_at` → `enabled = 0` |
| `customercategories` | `Kodafreight Contact Category` | per-client dedupe |
| `customers` / `customerleads` | `Kodafreight Contact` (`source = Customer/Lead`) | phone normalised to MSISDN, invalid numbers skipped and counted |
| `messages` | `Kodafreight SMS Message` | status mapping `pending→Queued`, `delivered→Sent`, `failed→Failed`; rows are inserted with validation bypassed so nothing is re-sent or re-charged |

Idempotency: every imported document stores the source primary key in `legacy_id`
(hidden, indexed); re-running skips what already exists. Use `limit_messages` to import the
history in slices.

### Post-migration steps

1. Review `Kodafreight Client` statuses (everything arrives as Active).
2. Set each client's default sender name if the legacy data had none.
3. Run `Kodafreight SMS Credit Account → Rebuild` for any client whose totals look off.
4. Trigger password resets for the migrated users (no passwords are carried over).
5. Point the provider's delivery-report URL at
   `https://<site>/api/method/kodafreight.api.webhooks.delivery_report` and configure the
   shared secret.

## 6. What is intentionally **not** migrated

The freight/finance tables (`loadpackages`, `containers`, `invoices`, `payments`,
`office_expenses`, `sales_reports`, `derivery_orders`, `godown_addresses`, `banks`,
`branches`, `patients`, …) are outside the SMS SaaS scope. ERPNext v16 covers those domains
natively (Stock, Selling, Accounts, HR); mapping them would duplicate core functionality.
They are listed here so that nothing is silently dropped — see
`docs/02-architecture-and-mapping.md` §2.
