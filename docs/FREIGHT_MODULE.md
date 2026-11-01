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

## 1b. The Cargo Load form

```
Load Details          customer, load_type, unit_type, branch, Ocean Mode, description, photo
Quantity / Measurement
                      quantity            <- labelled with the chosen unit (CBM, KG, pieces)
                      [ ] Measure Dimensions (L x W x H)   <- optional, computes CBM
                      billable quantity / billable CBM (read only), weight
Movement              transport date, delivery date, trucking number, container, status
Pricing               rate (per unit), exchange rate, transport fee, freight amount, total
Invoice               sales invoice, invoice status
```

* **Measurements are optional**, exactly as in the Laravel form. Nothing blocks data entry.
* **Money is never entered on the load.** `rate`, `exchange_rate`, `transport_fee`,
  `freight_amount` and `total_amount` are read-only on the Cargo Load; the pricing section is
  collapsed until an invoice exists. The clerk prices on the **Sales Invoice** - that is the
  legacy "update invoice" step - and reopening the load shows the figures back.
* **Ocean Mode** decides the shipping channel: ticked = ocean freight (may go into a
  Container), unticked = air freight (containers are refused).
* The rate is *per unit of the selected Unit Type*, so the same form prices CBM, KG or pieces.
  Only a volume unit uses CBM for container utilisation.

## 1c. Where the Laravel "edit invoice" screen went

| Laravel invoice edit page | ERPNext |
|---|---|
| Invoice Number / Status | Sales Invoice `name` + `status` |
| Load Weight Values | the item rows (qty = CBM/kg/pieces) |
| **CBM Price** | **Sales Invoice → Freight Pricing → CBM / Unit Price** |
| **Exchange Rate** | **Sales Invoice → Freight Pricing → Exchange Rate** |
| Amount to Pay (Total + Transport) | `Grand Total` |
| Total Received / Balance Due | `grand_total - outstanding_amount` / `outstanding_amount` |
| Record Payment | **Create → Record Payment** (creates a Payment Entry) |
| Payment History | the invoice's *Connections* / *Payment Ledger* + **Customer Statement** report |

The freight rate on the item row is always `CBM/Unit Price x Exchange Rate`; typing either
field re-prices the row immediately (client script + server validate hook).

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

## 5e. Where to set the invoice letterhead and the money details

Everything on the Final Invoice PDF is data you own, not code:

| What appears on the PDF | Where to set it |
|---|---|
| Company name, phone, email, TIN | **Company** record (`/app/company`) - *Company Name*, *Phone No*, *Email*, *Tax ID* |
| `Office Add: ...` | **Address** (`/app/address/new`) with a *Reference* row: Document Type = Company, Link Name = your company. Tick *Is Your Company Address* / set as primary. The print hook reads the primary address through `Dynamic Link` |
| **Payment Info** - where the client sends money | **Accounting → Bank Account → New**: *Account Name*, *Bank*, *Account No* / *IBAN*, Company = yours, **Is Company Account = Yes**. Up to three company accounts are printed automatically |
| Terms & Conditions | **Selling Settings → Default Terms and Conditions**, or the invoice's own *Terms* field |
| Prepared by (name, email, phone) | automatic - taken from the user who created the invoice |
| Amount Due / Exchange Rate / Total | the invoice itself: *Freight Pricing → CBM / Unit Price* and *Exchange Rate* |

With an **Exchange Rate greater than 1**, the item table prints `Unit Price ($)` and
`Total USD ($)` alongside `Amount Due (TZS)`, matching the legacy invoice layout. With
Rate = 1 the table prints a single currency, with no empty dollar columns.

## 5f. "A field/section is in the code but not in the UI"

Frappe only re-syncs a DocType when the JSON `modified` is newer than the record in the
database, so a pulled change can silently not appear. Two commands fix it:

```bash
# reload one doctype immediately (no full migrate)
bench --site <site> reload-doc kodafreight doctype kodafreight_settings

# or force it from the console
bench --site <site> console
>>> import frappe
>>> frappe.reload_doc("kodafreight", "doctype", "kodafreight_settings", force=True)
```

`after_migrate` now force-reloads `Kodafreight Settings` on every migrate, because the
"Invoice Defaults" section was lost to exactly this problem once.

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
