app_name = "kodafreight"
app_title = "Kodafreight"
app_publisher = "Kodafreight"
app_description = "Multi-tenant SMS SaaS platform (migrated from a Laravel application)"
app_email = "support@kodafreight.local"
app_license = "MIT"
app_version = "0.1.0"

# Frappe / ERPNext version 16
required_apps = []

# Ship dashboard records that live outside a doctype folder. Frappe's module sync only walks
# `IMPORTABLE_DOCTYPES` plus whatever an app adds here, so without these two entries the
# number cards and the volume chart would never be imported on install/migrate.
importable_doctypes = ["Number Card", "Dashboard Chart"]

# ------------------------------------------------------------------------------
# Assets
# ------------------------------------------------------------------------------
app_include_css = "/assets/kodafreight/css/kodafreight.css"
app_include_js = "/assets/kodafreight/js/kodafreight.js"

web_include_css = "/assets/kodafreight/css/kodafreight_portal.css"

# ------------------------------------------------------------------------------
# Installation
# ------------------------------------------------------------------------------
after_install = "kodafreight.install.after_install"
after_migrate = "kodafreight.install.after_migrate"

# ------------------------------------------------------------------------------
# Website / portal
# ------------------------------------------------------------------------------
website_route_rules = [
    {"from_route": "/kodafreight", "to_route": "kodafreight"},
]

# Client users land on the SMS workspace of the desk.
role_home_page = {
    "Kodafreight Client Admin": "app/kodafreight",
    "Kodafreight SMS Manager": "app/kodafreight",
    "Kodafreight Client Staff": "app/kodafreight",
}

# ------------------------------------------------------------------------------
# Permissions (tenant isolation - see kodafreight/permissions.py)
# ------------------------------------------------------------------------------
permission_query_conditions = {
    "Kodafreight Client": "kodafreight.permissions.client_query_conditions",
    "Kodafreight Sender Name": "kodafreight.permissions.sender_name_query_conditions",
    "Kodafreight SMS Message": "kodafreight.permissions.sms_message_query",
    "Kodafreight SMS Campaign": "kodafreight.permissions.sms_campaign_query",
    "Kodafreight Contact": "kodafreight.permissions.contact_query",
    "Kodafreight Contact Category": "kodafreight.permissions.contact_category_query",
    "Kodafreight Client User": "kodafreight.permissions.client_user_query",
    "Kodafreight SMS Credit Account": "kodafreight.permissions.credit_account_query",
    "Kodafreight SMS Credit Transaction": "kodafreight.permissions.credit_transaction_query",
    "Kodafreight SMS Delivery Log": "kodafreight.permissions.delivery_log_query",
    "Kodafreight SMS Number": "kodafreight.permissions.sms_number_query",
    "Kodafreight Subscription": "kodafreight.permissions.subscription_query",
    "Kodafreight Audit Log": "kodafreight.permissions.audit_log_query",
}

has_permission = {
    "Kodafreight Client": "kodafreight.permissions.client_has_permission",
    "Kodafreight Sender Name": "kodafreight.permissions.sender_name_has_permission",
    "Kodafreight SMS Message": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight SMS Campaign": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight Contact": "kodafreight.permissions.contact_has_permission",
    "Kodafreight Contact Category": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight Client User": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight SMS Credit Account": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight SMS Credit Transaction": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight SMS Delivery Log": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight SMS Number": "kodafreight.permissions.has_tenant_permission",
    "Kodafreight Subscription": "kodafreight.permissions.has_tenant_permission",
}

# ------------------------------------------------------------------------------
# Document events
# ------------------------------------------------------------------------------
doc_events = {
    "Kodafreight SMS Message": {
        "after_insert": "kodafreight.events.on_message_created",
    },
    "User": {
        "on_update": "kodafreight.events.on_user_update",
    },
}

# ------------------------------------------------------------------------------
# Scheduled tasks (Laravel: `messages:send-pending` every minute)
# ------------------------------------------------------------------------------
scheduler_events = {
    "cron": {
        "* * * * *": [
            "kodafreight.tasks.process_pending_messages",
        ],
    },
    "hourly": [
        "kodafreight.tasks.expire_subscriptions",
        "kodafreight.tasks.notify_low_balances",
    ],
    "daily": [
        "kodafreight.tasks.expire_credits",
        "kodafreight.tasks.cleanup_delivery_logs",
    ],
}

# ------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------
fixtures = [
    {"dt": "Role", "filters": [["name", "like", "Kodafreight%"]]},
]

# ------------------------------------------------------------------------------
# Testing
# ------------------------------------------------------------------------------
before_tests = "kodafreight.install.before_tests"

# ------------------------------------------------------------------------------
# Jinja
# ------------------------------------------------------------------------------
jinja = {
    "methods": [
        "kodafreight.utils.jinja.sms_status_badge",
    ],
}
