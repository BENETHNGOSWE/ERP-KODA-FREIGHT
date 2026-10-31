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
