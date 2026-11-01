"""Installation hooks: roles, default settings, provider and demo-safe defaults."""

import frappe

ROLES = [
    ("Kodafreight Platform Admin", "Owns the SaaS platform: clients, providers, senders, credits."),
    ("Kodafreight Client Admin", "Administrator of one client/tenant."),
    ("Kodafreight SMS Manager", "SMS operations for one client (legacy `kodasmspackage`)."),
    ("Kodafreight Client Staff", "Restricted staff user of one client."),
    # --- freight roles: one per desk in the Laravel permission matrix --------------
    ("Kodafreight Operations", "Creates cargo loads and containers, submits release orders."),
    ("Kodafreight Cashier", "Records payments and reads financial reports."),
    ("Kodafreight Godown Manager", "Releases cargo and raises delivery orders."),
]

DEFAULT_CLIENT_TYPES = ["Freight", "Retail", "Service", "Other"]


def after_install():
    create_roles()
    create_client_types()
    create_default_provider()
    create_settings()
    setup_freight()
    frappe.db.commit()
    print("Kodafreight installed.")


def after_migrate():
    # Every step is idempotent, and none of them is allowed to abort `bench migrate`:
    # a failed provisioning step is logged and reported, and the site still migrates.
    safe(create_roles, "create roles")
    safe(create_settings, "create settings")
    safe(setup_freight, "freight setup")
    frappe.db.commit()


def safe(step, label):
    """Run a provisioning step, log instead of raising.

    `bench migrate` runs this through `post_schema_updates`; an exception here aborts the
    entire migration with a stack trace that looks like a framework failure even though the
    schema is already updated. Provisioning is best-effort by design.
    """
    try:
        step()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(
            title="Kodafreight setup step failed: {0}".format(label),
            message=frappe.get_traceback(),
        )
        print("Kodafreight: '{0}' failed - see Error Log. Run `bench --site <site> "
              "execute kodafreight.install.setup_freight` to retry.".format(label))


def setup_freight():
    """Provision everything the freight module needs from ERPNext."""
    if not frappe.db.exists("DocType", "Sales Invoice"):
        print("ERPNext not installed - freight invoicing will not work until it is.")
        return
    safe(create_uoms, "UOMs")
    safe(create_freight_items, "freight items")
    safe(create_freight_custom_fields, "custom fields")
    safe(create_freight_reference_data, "reference data")


def create_uoms():
    # "Nos" and "Kilogram" ship with ERPNext; "Cubic Meter" usually does not, and the
    # freight invoice rows price in m³.
    for uom in ("Cubic Meter", "Kilogram", "Nos"):
        if not frappe.db.exists("UOM", uom):
            frappe.get_doc({"doctype": "UOM", "uom_name": uom, "enabled": 1}).insert(
                ignore_permissions=True
            )


def create_freight_items():
    """The two service items every freight invoice is built from.

    Laravel had no items at all - prices were typed straight onto an invoice. ERPNext
    needs an Item so the revenue lands in an income account.
    """
    if not frappe.db.exists("DocType", "Item"):
        return
    item_group = ensure_item_group()
    defaults = [
        ("Freight Charges", "Freight charges per cubic metre", "Cubic Meter"),
        ("Transport Charges", "Trucking / transport fee", "Nos"),
    ]
    for item_code, description, uom in defaults:
        if frappe.db.exists("Item", item_code):
            continue
        if not frappe.db.exists("UOM", uom):
            uom = frappe.db.get_value("UOM", {"enabled": 1}, "name") or uom
        frappe.get_doc({
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": item_group,
            "description": description,
            "is_stock_item": 0,
            "is_sales_item": 1,
            "is_purchase_item": 0,
            "stock_uom": uom,
            "include_item_in_manufacturing": 0,
        }).insert(ignore_permissions=True)


def ensure_item_group():
    """Return a usable (non-group) Item Group for the freight service items.

    The previous version guessed with `get_value("Item Group", {"is_group": 1})`, which can
    return `None` on a freshly installed ERPNext - and `Item.item_group` is mandatory, so
    `bench migrate` died with `MandatoryError: [Item, ...]: item_group`.
    """
    for candidate in ("Freight Services", "Services", "Products"):
        if frappe.db.exists("Item Group", candidate):
            if not frappe.db.get_value("Item Group", candidate, "is_group"):
                return candidate

    parent = None
    for candidate in ("All Item Groups", "Services", "Products"):
        if frappe.db.exists("Item Group", candidate):
            parent = candidate
            break

    doc = frappe.get_doc({
        "doctype": "Item Group",
        "item_group_name": "Freight Services",
        "parent_item_group": parent or "",
        "is_group": 0,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def create_freight_custom_fields():
    """Fields added to ERPNext's own documents.

    * `Customer.kf_client`      - makes a Customer belong to one tenant.
    * `Sales Invoice.kf_cargo_load` - links the bill back to the cargo load.
    * item-level link so a merged/reprinted invoice still shows which loads it covers.
    """
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

    fields = {
        "Branch": [
            {
                "fieldname": "kf_client",
                "label": "Kodafreight Client",
                "fieldtype": "Link",
                "options": "Kodafreight Client",
                "insert_after": "branch",
                "in_standard_filter": 1,
                "search_index": 1,
                "description": "Tenant that owns this branch. Set automatically on creation.",
            }
        ],
        "Customer": [
            {
                "fieldname": "kf_client",
                "label": "Kodafreight Client",
                "fieldtype": "Link",
                "options": "Kodafreight Client",
                "insert_after": "customer_name",
                "in_standard_filter": 1,
                "search_index": 1,
                "description": "Tenant that owns this customer. Set automatically on creation.",
            }
        ],
        "Sales Invoice": [
            {
                "fieldname": "kf_freight_pricing",
                "label": "Freight Pricing",
                "fieldtype": "Section Break",
                "insert_after": "items",
                "collapsible": 1,
                "description": "The Laravel invoice form: CBM price and exchange rate. The freight row rate is computed from them.",
            },
            {
                "fieldname": "kf_cbm_price",
                "label": "CBM / Unit Price",
                "fieldtype": "Currency",
                "insert_after": "kf_freight_pricing",
                "description": "Price per unit (CBM, kg, piece) BEFORE the exchange rate.",
            },
            {
                "fieldname": "kf_price_column",
                "label": "",
                "fieldtype": "Column Break",
                "insert_after": "kf_cbm_price",
            },
            {
                "fieldname": "kf_exchange_rate",
                "label": "Exchange Rate",
                "fieldtype": "Float",
                "precision": "6",
                "default": "1",
                "insert_after": "kf_price_column",
                "description": "Freight rate = CBM/Unit Price x Exchange Rate. 1 = no conversion.",
            },
            {
                "fieldname": "kf_print_context",
                "label": "Print Context",
                "fieldtype": "Section Break",
                "insert_after": "kf_exchange_rate",
                "collapsible": 1,
                "collapsed": 1,
            },
            {
                "fieldname": "kf_company_name",
                "label": "Company Name (print)",
                "fieldtype": "Data",
                "insert_after": "kf_print_context",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_company_phone",
                "label": "Company Phone (print)",
                "fieldtype": "Data",
                "insert_after": "kf_print_context",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_company_tin",
                "label": "Company TIN (print)",
                "fieldtype": "Data",
                "insert_after": "kf_company_phone",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_company_email",
                "label": "Company Email (print)",
                "fieldtype": "Data",
                "insert_after": "kf_company_tin",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_company_address",
                "label": "Company Address (print)",
                "fieldtype": "Small Text",
                "insert_after": "kf_company_tin",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_payment_info",
                "label": "Bank / Payment Info (print)",
                "fieldtype": "Small Text",
                "insert_after": "kf_company_address",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_prepared_by",
                "label": "Prepared By (print)",
                "fieldtype": "Data",
                "insert_after": "kf_payment_info",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_prepared_contact",
                "label": "Prepared By Contact (print)",
                "fieldtype": "Data",
                "insert_after": "kf_prepared_by",
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": "kf_cargo_load",
                "label": "Cargo Load",
                "fieldtype": "Link",
                "options": "Cargo Load",
                "insert_after": "kf_payment_info",
                "read_only": 1,
                "no_copy": 1,
                "print_hide": 1,
                "search_index": 1,
            }
        ],
        "Sales Invoice Item": [
            {
                "fieldname": "kf_cargo_load",
                "label": "Cargo Load",
                "fieldtype": "Link",
                "options": "Cargo Load",
                "insert_after": "item_name",
                "read_only": 1,
                "hidden": 1,
                "no_copy": 1,
                "print_hide": 1,
            }
        ],
    }
    create_custom_fields(fields, ignore_validate=True)


def create_freight_reference_data():
    load_types = [
        ("General Cargo", "Mixed / loose general cargo"),
        ("Machinery", "Spare parts, machinery, equipment"),
        ("Electronics", "Phones, appliances, accessories"),
        ("Textiles", "Fabrics, garments, shoes"),
        ("Building Materials", "Tiles, fittings, hardware"),
        ("Vehicles", "Cars, bikes, auto parts"),
    ]
    for name, description in load_types:
        if not frappe.db.exists("Load Type", name):
            frappe.get_doc({
                "doctype": "Load Type",
                "load_type_name": name,
                "description": description,
                "enabled": 1,
            }).insert(ignore_permissions=True)

    unit_types = [("CBM", "m³"), ("KG", "kg"), ("PCS", "pcs")]
    for name, symbol in unit_types:
        if not frappe.db.exists("Unit Type", name):
            frappe.get_doc({
                "doctype": "Unit Type",
                "unit_name": name,
                "symbol": symbol,
                "enabled": 1,
            }).insert(ignore_permissions=True)


def create_roles():
    for name, desc in ROLES:
        if frappe.db.exists("Role", name):
            continue
        frappe.get_doc({
            "doctype": "Role",
            "role_name": name,
            "desk_access": 1,
            "is_custom": 1,
            "search_bar": 1,
            "notifications": 1,
            "list_sidebar": 1,
            "form_sidebar": 1,
            "timeline": 1,
            "dashboard": 1,
        }).insert(ignore_permissions=True)


def create_client_types():
    for t in DEFAULT_CLIENT_TYPES:
        if not frappe.db.exists("Kodafreight Client Type", t):
            frappe.get_doc({"doctype": "Kodafreight Client Type", "type_name": t}).insert(
                ignore_permissions=True)


def create_default_provider():
    """Kilakona - the provider the Laravel system uses. Credentials are left blank on
    purpose: they must be entered by the platform administrator, never shipped in code."""
    name = "Kilakona"
    if frappe.db.exists("Kodafreight SMS Provider", name):
        return
    frappe.get_doc({
        "doctype": "Kodafreight SMS Provider",
        "provider_name": name,
        "driver": "Kilakona",
        "enabled": 1,
        "is_default": 1,
        "api_base_url": "https://messaging.kilakona.co.tz",
        "send_endpoint": "/api/v1/vendor/message/send",
        "auth_type": "API Key Header",
        "timeout": 30,
        "verify_ssl": 1,
        "max_recipients_per_request": 100,
        "notes": "Set api_key / api_secret before sending. Migrated from the Laravel "
                 "SMS_API_KEY2 / SMS_API_SECRET2 environment variables.",
    }).insert(ignore_permissions=True)


def create_settings():
    settings = frappe.get_single("Kodafreight Settings")
    changed = False
    if not settings.default_provider and frappe.db.exists("Kodafreight SMS Provider", "Kilakona"):
        settings.default_provider = "Kilakona"
        changed = True
    if not settings.default_country_code:
        settings.default_country_code = "255"
        changed = True
    if not settings.national_number_length:
        settings.national_number_length = 9
        changed = True
    if not settings.queue_batch_size:
        settings.queue_batch_size = 20
        changed = True
    if not settings.webhook_secret:
        settings.webhook_secret = frappe.generate_hash(length=40)
        changed = True
    if changed:
        settings.flags.ignore_permissions = True
        settings.save(ignore_permissions=True)


def before_tests():
    """Prepare a site for `bench run-tests --app kodafreight`."""
    frappe.clear_cache()
    after_install()
    frappe.db.commit()
