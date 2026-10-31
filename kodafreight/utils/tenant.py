"""Tenant helpers - the single source of truth for "which client is this user?"."""

import frappe

PLATFORM_ROLES = {"Kodafreight Platform Admin", "Administrator", "System Manager"}
CLIENT_ROLES = (
    "Kodafreight Client Admin",
    "Kodafreight SMS Manager",
    "Kodafreight Client Staff",
)

# DocTypes that carry a `client` link field and must be tenant filtered.
TENANT_DOCTYPES = (
    "Kodafreight Client User",
    "Kodafreight Branch",
    "Kodafreight Contact",
    "Kodafreight Contact Category",
    "Kodafreight SMS Message",
    "Kodafreight SMS Campaign",
    "Kodafreight SMS Credit Account",
    "Kodafreight SMS Credit Transaction",
    "Kodafreight SMS Delivery Log",
    "Kodafreight SMS Number",
    "Kodafreight Subscription",
    "Kodafreight Audit Log",
)


def is_platform_admin(user=None) -> bool:
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = set(frappe.get_roles(user))
    return bool(roles & PLATFORM_ROLES)


def get_user_client(user=None, throw=False):
    """Return the `Kodafreight Client` a user belongs to (None for platform users)."""
    user = user or frappe.session.user
    if user in ("Administrator", "Guest"):
        return None

    cached = frappe.cache().hget("kodafreight_user_client", user)
    if cached is not None:
        return cached or None

    client = frappe.db.get_value(
        "Kodafreight Client User", {"user": user, "enabled": 1}, "client"
    )
    frappe.cache().hset("kodafreight_user_client", user, client or "")

    if not client and throw:
        frappe.throw(frappe._("Your user is not linked to any Kodafreight client."),
                     frappe.PermissionError)
    return client


def clear_user_client_cache(user=None):
    if user:
        frappe.cache().hdel("kodafreight_user_client", user)
    else:
        frappe.cache().delete_key("kodafreight_user_client")


def resolve_client(client=None, throw=True):
    """Resolve the client to act on.

    Platform admins may pass any client explicitly; tenant users are always forced onto
    their own client, whatever the request payload contains.
    """
    own = get_user_client()
    if own:
        if client and client != own:
            frappe.throw(
                frappe._("You are not permitted to access data of another client."),
                frappe.PermissionError,
            )
        return own
    if is_platform_admin():
        if not client and throw:
            frappe.throw(frappe._("Client is required."))
        return client
    if throw:
        frappe.throw(frappe._("Your user is not linked to any Kodafreight client."),
                     frappe.PermissionError)
    return None


def ensure_client_active(client):
    """Block operations for inactive/suspended tenants (Laravel had no such control)."""
    status, sms_enabled = frappe.db.get_value(
        "Kodafreight Client", client, ["status", "sms_enabled"]
    ) or (None, None)
    if status is None:
        frappe.throw(frappe._("Client {0} does not exist.").format(client))
    if status != "Active":
        frappe.throw(frappe._("Client {0} is {1}. SMS operations are blocked.").format(
            client, status.lower()))
    if not sms_enabled:
        frappe.throw(frappe._("SMS is disabled for client {0}.").format(client))
    return True


def get_client_senders(client, only_enabled=True):
    """Sender names assigned to a client (mirrors Laravel `client_sendername`)."""
    doc = frappe.get_cached_doc("Kodafreight Client", client)
    rows = []
    for row in doc.senders or []:
        if only_enabled and not row.enabled:
            continue
        status = frappe.db.get_value("Kodafreight Sender Name", row.sender_name, "status")
        if only_enabled and status != "Active":
            continue
        rows.append({"sender_name": row.sender_name, "is_default": bool(row.is_default)})
    return rows


def assert_sender_allowed(client, sender_name):
    """Server-side guard the Laravel system lacked: a client may only use its own senders."""
    allowed = {r["sender_name"] for r in get_client_senders(client)}
    if sender_name not in allowed:
        frappe.throw(
            frappe._("Sender name '{0}' is not assigned to (or is not active for) client {1}.")
            .format(sender_name, client),
            frappe.PermissionError,
        )
    return True


def get_default_sender(client):
    senders = get_client_senders(client)
    for row in senders:
        if row["is_default"]:
            return row["sender_name"]
    return senders[0]["sender_name"] if senders else None
