# Freight Operations Module

The freight half of the legacy Laravel system, rebuilt on ERPNext. The SMS module is
unchanged; this module adds the cargo → container → invoice → release → delivery chain.

---

## 1. Where the Laravel flow went

| Laravel | Kodafreight / ERPNext | Notes |
|---|---|---|
| `Loadpackage` | **Cargo Load** | weight, L×W×H, CBM, type, dates, trucking no, photo |
| `LoadPackageHelper::createLoad()` (`load_volume = L×W×H`) | `services/freight.py: compute_volume()` | measured CBM is always shown |
| typed `load_volume` column | `use_manual_volume` + `manual_volume` | one explicit source of truth (the old code had two that disagreed) |
| `Container` + `Containerloading` | **Container** + child **Container Load** | capacity/fill % enforced, load can be in one container only |
| `Invoice` / `invoice_loads` | **Sales Invoice** (+ `kf_cargo_load` custom field) | real ERPNext ledger document |
| `cbm_price` × `exchange_rate` | `Cargo Load.cbm_rate` × `exchange_rate`, prefilled from **Freight Tariff** | rate lives in one place per client |
| `total_amount + total_transportfee` | `freight_amount + transport_fee` → Sales Invoice rows | amount due is unchanged |
| invoice `status` 0/1/2 typed by a human | `outstanding_amount` from Payment Entry | derived from money, never typed |
| `InvoicePayment` table | **Payment Entry** (native) | audited, allocated, reversible |
| Release Order blocked on `status != 1` | **Release Order** refuses to save unless nothing is outstanding | rule moved onto the document, not a print URL |
| `DeriveryOrder` (driver/phone/vehicle) | **Delivery Order** | same three mandatory fields + paid-invoice gate |
| `CustomerStatement` (rebuilt report) | **Customer Statement** | Due / Received / Balance from real allocations |
| CBM Record screen | **CBM Record** | volume + revenue per load |
| `InvoiceMerge` (combined PDF) | *Merge / Combined Bill (PDF)* action on the Sales Invoice list | originals untouched |
| `rolesAllowed` hard-coding | Frappe roles + permissions | no string comparisons |
| `where('client_id', ...)` in every controller | `permission_query_conditions` + `has_permission` for every freight DocType, plus `Customer` and `Sales Invoice` | one place, cannot be forgotten |

---

## 2. The flow as implemented

```
Customer (ERPNext, tenant-stamped with kf_client)
   │
   ▼
Cargo Load (draft)  ──  measurements → CBM  ──  tariff → rate × FX (+ transport)
   │  submit
   ▼
Sales Invoice (draft)  ←── auto-created, one per load, only when actually priced
   │  submit
   ▼
Payment Entry(s)  ──  outstanding_amount falls  ──  Cargo Load.invoice_status follows
   │  outstanding == 0
   ▼
Release Order (refused unless paid)  ──►  print "Kodafreight Release Order"
   │
   ▼
Delivery Order (driver / phone / vehicle)  ──►  loads become "Delivered"
```

Container is orthogonal: loads are attached to a **Container** before or after
invoicing; submitting the container stamps `container` + `shipment_status = In Container`
on the loads, and capacity is enforced.

---

## 3. Rules now enforced in code (each one was a live bug in Laravel)

1. **No silent zero volumes** — dimensions or an explicit manual CBM are required, so an
   invoice can never be 0.00 because a clerk left three boxes blank.
2. **No unpriced invoices** — a load stays *Pending Pricing* until a rate or a fee exists.
3. **No typed payment status** — status comes from `outstanding_amount`.
4. **No release without payment** — enforced on the Release Order document itself.
5. **One container per load** and **capacity cannot be exceeded**.
6. **Tenant isolation on ERPNext documents** — `Customer` and `Sales Invoice` are filtered
   per client through `Customer.kf_client`; a tenant user cannot list or open another
   tenant's customers or invoices, and cannot forge `client` on save.
7. **Cancellations respect money** — a load whose invoice already has payments recorded
   cannot be cancelled.

---

## 4. Install / upgrade

ERPNext is now a hard requirement (`required_apps = ["frappe/erpnext"]`).

```bash
cd ~/frappe-bench
bench get-app erpnext           # if not already installed
bench --site <site> install-app erpnext
bench get-app kodafreight <git-url>
bench --site <site> install-app kodafreight
bench --site <site> migrate     # creates roles, items, custom fields, reference data
bench --site <site> clear-cache
bench build --app kodafreight
bench restart
```

`after_migrate` is idempotent and provisions:

* roles `Kodafreight Operations`, `Kodafreight Cashier`, `Kodafreight Godown Manager`
* UOMs `Cubic Meter`, `Kilogram`
* items `Freight Charges`, `Transport Charges`
* custom fields `Customer.kf_client`, `Sales Invoice.kf_cargo_load`,
  `Sales Invoice Item.kf_cargo_load`
* reference data: load types and unit types

### Post-install checklist

1. **Company** + Chart of Accounts exist, and **Global Defaults → default company** is set
   (invoicing throws a clear error without it).
2. Enter a **Freight Tariff** per client (CBM rate, exchange rate, transport fee)
   so clerks do not retype prices.
3. Create **Kodafreight Client Users** for the freight staff and give them the matching role.
4. Set **Kodafreight Client → Branch** records if you use branches.
5. Warehouses exist if you want to name the godown on release/delivery documents.

---

## 5. Printing

Standard print formats shipped with the app (all Jinja, editable in the UI):

| Document | Print format |
|---|---|
| Cargo Load | *Cargo Receipt* |
| Sales Invoice | *Final Invoice* |
| Release Order | *Release Order* |
| Delivery Order | *Delivery Order* |
| Several invoices, one customer | *Merge / Combined Bill (PDF)* list action |

---

## 5c. Naming

The freight doctypes carry no `Kodafreight ` prefix - they are **Cargo Load**,
**Container**, **Container Load**, **Freight Tariff**, **Release Order**,
**Delivery Order**, **Load Type**, **Unit Type**, plus the reports **Customer Statement**
and **CBM Record**, all on the workspace *Configuration* / *Freight* cards.
The SMS module keeps its `Kodafreight SMS ...` names (they are shared with the platform
side and covered by the existing test suite).
**Branch, Warehouse and Company are ERPNext's own doctypes**, tenant-scoped with a
`kf_client` custom field, instead of being re-implemented.

## 5d. Laravel "Settings" menu -> where it lives now

| Laravel setting (company admin) | Kodafreight / ERPNext | Who can edit |
|---|---|---|
| Roles | **Role** (Frappe, global list) + per-staff **Kodafreight Client User → User Role** | Platform admin creates roles; client admin assigns the shipped freight roles |
| Staffs | **Kodafreight Client User** | Client admin (tenant-scoped by permissions) |
| Branches | **Branch** (ERPNext native, tenant-scoped by `kf_client`) | Client admin |
| Units | **Unit Type** + **UOM** (ERPNext accounting unit) | Client admin |
| Loads Types | **Load Type** | Client admin |
| CBM rate / exchange rate (was per-invoice) | **Freight Tariff** | Client admin |
| Payment Information (bank accounts) | **Bank Account** (+ **Mode of Payment**) | Client admin (needs Company + Accounts) |
| Godown Details | **Warehouse** | Client admin |
| Invoice Details (letterhead) | **Company** (name, TIN, address, logo) + **Letter Head** | Client admin |
| Active Name / my sender names | **Kodafreight Sender Name** (list is filtered to the senders assigned to your client) | Platform assigns; client sees |

Naming and numbering series, taxes and the chart of accounts come from ERPNext itself
(**Accounting → Chart of Accounts**, **Selling Settings**).

## 5b. Editing the shipped workspace

Frappe only re-imports a standard Workspace when the JSON `modified` timestamp is **newer**
than the record in the database. If you add cards/links to
`kodafreight/kodafreight/workspace/kodafreight/kodafreight.json` and nothing changes after
`bench migrate`, bump `modified` and re-run:

```bash
git pull && bench --site <site> migrate && bench --site <site> clear-cache && bench restart
```

If the workspace is still stale, delete the record and let migrate recreate it:

```bash
bench --site <site> execute frappe.delete_doc --args '["Workspace", "Kodafreight"]'
bench --site <site> migrate
```

## 6. Tests

```bash
python3 tests_offline/validate_app.py   # 29 doctypes, link integrity, hooks, secrets
python3 tests_offline/run_tests.py      # 61 tests (47 SMS + 14 freight)
```

The freight tests pin the legacy arithmetic — including the real invoice
`234 CBM × 2750 × 20 = 12,870,000` — and the payment gate (`assert_invoice_paid`).

---

## 7. Deliberately not built yet

| Item | Why / what it needs |
|---|---|
| Data import from the Laravel MySQL database | needs a mapping run on the real data (customers, loads, invoices, payments). `utils/migrate_from_laravel.py` already exists for the SMS side |
| SMS templates tied to freight events (arrival, release ready) | the SMS engine exists; wire `Kodafreight SMS Message` to Cargo Load status changes |
| Customer portal ("Track your load") | needs a `www/` page + login for customers; the Laravel version was a load-number lookup |
| Branch-level accounting dimensions | add `Branch` as a custom Cost Center / Accounting Dimension on Sales Invoice |
| Multi-currency invoicing | currently the FX rate is applied to the rate itself (Laravel behaviour). Use ERPNext multi-currency if true FX accounting is needed |
