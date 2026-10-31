"""Installation hooks: roles, default settings, provider and demo-safe defaults."""

import frappe

ROLES = [
    ("Kodafreight Platform Admin", "Owns the SaaS platform: clients, providers, senders, credits."),
    ("Kodafreight Client Admin", "Administrator of one client/tenant."),
    ("Kodafreight SMS Manager", "SMS operations for one client (legacy `kodasmspackage`)."),
    ("Kodafreight Client Staff", "Restricted staff user of one client."),
]

DEFAULT_CLIENT_TYPES = ["Freight", "Retail", "Service", "Other"]


def after_install():
    create_roles()
    create_client_types()
    create_default_provider()
    create_settings()
    frappe.db.commit()
    print("Kodafreight 0.1.0 installed.")


def after_migrate():
    create_roles()
    create_settings()
    frappe.db.commit()


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
