"""Tenant isolation enforced on the server.

Three layers protect client data (the Laravel system relied on a single, easily forgotten
`where('client_id', ...)` in each controller):

  1. `User Permission` rows (created by `Kodafreight Client User`) - Frappe applies them to
     every DocType with a `client` link field.
  2. `permission_query_conditions` (this module) - every list/report/count query for a
     tenant DocType gets an explicit SQL condition appended.
  3. `has_permission` (this module) - per document check, also used by the REST API.

Controllers additionally force `doc.client = get_user_client()` on write, so a forged
payload cannot create a record inside another tenant.
"""

import frappe

from kodafreight.utils.tenant import get_user_client, is_platform_admin

STAFF_ROLE = "Kodafreight Client Staff"


def _condition(doctype, client):
    return f"`tab{doctype}`.`client` = {frappe.db.escape(client)}"


def build_query_conditions(doctype):
    """Factory used by hooks for each tenant DocType."""

    def conditions(user=None, doctype=doctype):
        user = user or frappe.session.user
        if is_platform_admin(user):
            return ""
        client = get_user_client(user)
        if not client:
            # A logged-in user without a client sees nothing from tenant tables.
            return f"`tab{doctype}`.`client` = '__no_client__'"
        return _condition(doctype, client)

    return conditions


def has_tenant_permission(doc, ptype=None, user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    client = get_user_client(user)
    if not client:
        return False
    doc_client = doc.get("client") if hasattr(doc, "get") else None
    if doc_client and doc_client != client:
        return False
    return True


def client_has_permission(doc, ptype=None, user=None):
    """`Kodafreight Client` itself: a tenant user may only read their own client record."""
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    client = get_user_client(user)
    if not client:
        return False
    if doc.name != client:
        return False
    return ptype in (None, "read", "print", "email", "select")


def client_query_conditions(user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return ""
    client = get_user_client(user)
    if not client:
        return "`tabKodafreight Client`.`name` = '__no_client__'"
    return f"`tabKodafreight Client`.`name` = {frappe.db.escape(client)}"


def sender_name_query_conditions(user=None):
    """Clients only see the sender names assigned to them (Laravel `whereHas('clients')`)."""
    user = user or frappe.session.user
    if is_platform_admin(user):
        return ""
    client = get_user_client(user)
    if not client:
        return "`tabKodafreight Sender Name`.`name` = '__no_sender__'"
    return (
        "`tabKodafreight Sender Name`.`name` in ("
        " select sender_name from `tabKodafreight Client Sender`"
        f" where parent = {frappe.db.escape(client)} and enabled = 1)"
    )


def sender_name_has_permission(doc, ptype=None, user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    client = get_user_client(user)
    if not client:
        return False
    if ptype not in (None, "read", "select", "print", "email"):
        return False
    return bool(
        frappe.db.exists(
            "Kodafreight Client Sender",
            {"parent": client, "sender_name": doc.name, "enabled": 1},
        )
    )


# -- generated condition callables (referenced from hooks.py) -----------------------
sms_message_query = build_query_conditions("Kodafreight SMS Message")
sms_campaign_query = build_query_conditions("Kodafreight SMS Campaign")
contact_query = build_query_conditions("Kodafreight Contact")
contact_category_query = build_query_conditions("Kodafreight Contact Category")
client_user_query = build_query_conditions("Kodafreight Client User")
credit_account_query = build_query_conditions("Kodafreight SMS Credit Account")
credit_transaction_query = build_query_conditions("Kodafreight SMS Credit Transaction")
delivery_log_query = build_query_conditions("Kodafreight SMS Delivery Log")
sms_number_query = build_query_conditions("Kodafreight SMS Number")
subscription_query = build_query_conditions("Kodafreight Subscription")
audit_log_query = build_query_conditions("Kodafreight Audit Log")


def contact_has_permission(doc, ptype=None, user=None):
    """Client Staff may only touch their own contacts (Laravel: user_id = auth()->id())."""
    if not has_tenant_permission(doc, ptype, user):
        return False
    user = user or frappe.session.user
    roles = set(frappe.get_roles(user))
    if STAFF_ROLE in roles and not (
        roles & {"Kodafreight Client Admin", "Kodafreight SMS Manager",
                 "Kodafreight Platform Admin", "System Manager"}
    ):
        if ptype in ("write", "delete", "submit") and doc.get("assigned_to") not in (
            None, user
        ):
            return False
    return True


# ==============================================================================
# FREIGHT
# ==============================================================================
# Every freight doctype carries a `client` link, so the generic factory above covers
# them. ERPNext's own `Customer` and `Sales Invoice` need their own conditions because
# they link the tenant through `Customer.kf_client` instead of a `client` column - the
# same reason the Laravel `Customer::all()` leaked.
# ==============================================================================

cargo_load_query = build_query_conditions("Cargo Load")
container_query = build_query_conditions("Container")
freight_tariff_query = build_query_conditions("Freight Tariff")
release_order_query = build_query_conditions("Release Order")
delivery_order_query = build_query_conditions("Delivery Order")
load_type_query = build_query_conditions("Load Type")
unit_type_query = build_query_conditions("Unit Type")


def _tenant_customer_filter(alias="`tabCustomer`"):
    return (
        f"{alias}.`name` in (select name from `tabCustomer` "
        f"where kf_client = {{client}})"
    )


def branch_query_conditions(user=None):
    """Branches come from ERPNext; the tenant is carried by the `kf_client` field."""
    user = user or frappe.session.user
    if is_platform_admin(user):
        return ""
    client = get_user_client(user)
    if not client:
        return "`tabBranch`.`name` = '__no_branch__'"
    return f"`tabBranch`.`kf_client` = {frappe.db.escape(client)}"


def branch_has_permission(doc, ptype=None, user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    client = get_user_client(user)
    if not client:
        return False
    return (doc.get("kf_client") if hasattr(doc, "get") else None) == client


def customer_query_conditions(user=None):
    """Tenant users only see the Customers that belong to their client."""
    user = user or frappe.session.user
    if is_platform_admin(user):
        return ""
    client = get_user_client(user)
    if not client:
        return "`tabCustomer`.`name` = '__no_customer__'"
    return _tenant_customer_filter().format(client=frappe.db.escape(client))


def sales_invoice_query_conditions(user=None):
    """Sales Invoices are visible only when their customer is in the tenant."""
    user = user or frappe.session.user
    if is_platform_admin(user):
        return ""
    client = get_user_client(user)
    if not client:
        return "`tabSales Invoice`.`name` = '__no_invoice__'"
    return (
        "`tabSales Invoice`.`customer` in (select name from `tabCustomer` "
        f"where kf_client = {frappe.db.escape(client)})"
    )


def customer_has_permission(doc, ptype=None, user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    client = get_user_client(user)
    if not client:
        return False
    doc_client = doc.get("kf_client") if hasattr(doc, "get") else None
    # A customer with no tenant yet is platform-owned; a tenant user may not claim it.
    return doc_client == client


def sales_invoice_has_permission(doc, ptype=None, user=None):
    user = user or frappe.session.user
    if is_platform_admin(user):
        return True
    client = get_user_client(user)
    if not client:
        return False
    customer = doc.get("customer") if hasattr(doc, "get") else None
    if not customer:
        return False
    return frappe.db.get_value("Customer", customer, "kf_client") == client
