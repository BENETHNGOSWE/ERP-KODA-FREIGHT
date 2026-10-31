# Freight source review — work in progress

Source: `BENETHNGOSWE/FREIGHTMANAGEMENTSYSTEM`, GitHub HEAD `5b27c3372ccdc4de9e411730b2cc76d6e9d34307`, dated 2026-10-21. This review uses the GitHub revision rather than assuming the uploaded ZIP is current. It is an initial inspection, not a claim of complete parity or a finished implementation.

The GitHub revision differs from the earlier ZIP: it adds the `InvoicePayment` model and its 2026-10-21 migration, and changes invoice, delivery-order, financial-report and other code. The new source must be used when mapping the freight system.

## Confirmed workflow and source evidence

1. **Platform owner and clients** — `routes/owner.php`, `app/Http/Controllers/Owner/ClientController.php`: create clients and their administrator users; assign packages; impersonation exists. Preserve separation of platform and client accounts, but do not copy insecure password handling or unsafe impersonation blindly.
2. **Branch and staff** — `routes/admin.php`, `app/Http/Controllers/Admin/BranchController.php`, staff models and role-specific routes. Staff branch contributes to load numbering.
3. **Customers** — `routes/multiple.php`, `app/Http/Controllers/Admin/CustomerController.php`: create, edit, search, Excel import and template.
4. **Cargo registration** — `app/Http/Controllers/Admin/LoadpackageController.php` and `app/Helpers/LoadPackageHelper.php`: a submission creates an invoice, one or more cargo loads and invoice-load links, computes dimensions/volume, and sends customer notifications. Load numbers use the staff branch code plus a random suffix. Fields include tracking number, package type, unit, quantity/weight, dimensions, dates, transport mode and optional container. Exact validation, calculations, and status behavior require further inspection and tests.
5. **Containers and tracking** — `app/Http/Controllers/Admin/ContainerController.php`, `routes/multiple.php`: attach loads, load selection, container list PDF; statuses validated as Warehouse, OnVessel, Arrived_TZ-Port, ICD and Warehouse-HQ. Load tracking and status notifications also exist.
6. **Invoice and receipts** — `app/Http/Controllers/Admin/InvoiceController.php`: cargo receipt PDF, final invoice PDF, merge PDF. On invoice update the source computes total from sum of load weight values × CBM price × exchange rate. Initial load creation uses different total updates; reconcile deliberately rather than silently choosing one formula.
7. **Invoice payments** — `app/Models/InvoicePayment.php`, `database/migrations/2026_10_21_065231_create_invoice_payments_table.php`, `InvoiceController::recordPayment`: payment date, amount, method, reference and notes; blocks payment exceeding outstanding balance, derives payment status. Review concurrency and rounding when migrating.
8. **Release order** — `InvoiceController::generatereleasePDF`, `resources/views/dashboards/admin/invoices/releasepdf.blade.php`: requires the `print release order` permission and invoice status paid before generating the PDF. Must enforce eligibility server-side, not just in the UI.
9. **Delivery order** — `app/Http/Controllers/Godownmanager/DeriveryorderController.php`, delivery order views: invoice linked to client, driver name/number and vehicle registration; PDF exists. A delivery order and invoice release PDF are distinct source features.
10. **Notifications** — `LoadPackageHelper.php`: sends an SMS for warehouse receipt and another after container loading, with client sender and customer details. Integrate with the existing Kodafreight SMS system; reconcile failures and retries without double-sending.
11. **Additional scope** — client subscriptions and entitlements, sender names, offices/godowns, company invoice details, expenses, financial reports, sales, staff and role dashboards, and other routes/views. A full inventory is still required.

## ERPNext migration constraints

The target site currently has Frappe 16 and the SMS-focused Kodafreight app; ERPNext is not installed. Nothing in this review implies that branch, cargo, accounting, release or delivery workflows are implemented there yet. ERPNext accounting entities and custom freight DocTypes must be mapped carefully; a shared multi-tenant site needs server-side tenant isolation for both custom and standard ERPNext records. Do not deploy untested permissions or post live accounting transactions while this work is incomplete.

Suggested acceptance: an owner creates a client and administrator; the client creates a branch, staff and customer; cargo registration creates a linked invoice and notification; a container can be loaded and tracked; invoice and receipt are printable; partial and full payments update balance; only a paid invoice with permission produces a release order; a delivery order can be generated; each role sees only authorized tenant data. Preserve and test all additional functions found in the source inventory.

## Work still required

Complete the route/controller/model/view and permission inventory against this GitHub revision; document fields, documents and business rules; design and implement the ERPNext/Frappe mapping and migration; install ERPNext version 16 on a backed-up/test site; implement printable documents, tenant controls, accounting and SMS integration; run unit, bench, browser, tenant-isolation and end-to-end tests on the actual bench. No freight implementation is claimed complete by this review.

## Implementation progress — branch foundation

A first `Kodafreight Branch` DocType adds client, branch name and branch code, with server-side client checks, tenant list and document permission hooks, and a Freight Setup workspace link. Three new Frappe bench tests exercise tenant listing, cross-client creation, and changing an existing branch's client. Local checks: 47 existing offline checks pass; structural validation reports 21 DocTypes, 0 warnings, 0 errors; four branch controller checks pass with mocked Frappe. **New branch features and tests have not been run on a real bench.** Branch is an initial component, not the full freight flow, and is not yet connected to users, cargo, ERPNext accounting, or legacy migration. Do not use for production before further testing.
