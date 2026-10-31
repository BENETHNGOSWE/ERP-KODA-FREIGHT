# Copyright (c) 2026, Kodafreight and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, random_string

from kodafreight.utils import audit
from kodafreight.utils.tenant import clear_user_client_cache

ROLE_MAP = {
    "Client Admin": "Kodafreight Client Admin",
    "SMS Manager": "Kodafreight SMS Manager",
    "Client Staff": "Kodafreight Client Staff",
}
ALL_CLIENT_ROLES = tuple(ROLE_MAP.values())


class KodafreightClientUser(Document):
    def validate(self):
        self.enforce_tenant()
        self.email = (self.email or "").strip().lower()
        if not self.staff_no:
            self.staff_no = make_staff_no(self.client)

    def enforce_tenant(self):
        """A client admin can only create users inside their own client."""
        from kodafreight.utils.tenant import get_user_client, is_platform_admin

        own = get_user_client()
        if own:
            if self.client and self.client != own:
                frappe.throw(_("You can only manage users of your own client."),
                             frappe.PermissionError)
            self.client = own
            if self.user_role == "Client Admin" and "Kodafreight Client Admin" not in set(
                frappe.get_roles()
            ):
                frappe.throw(_("Only a client administrator can create another "
                               "client administrator."))
        elif not is_platform_admin():
            frappe.throw(_("Only platform administrators can create client users."),
                         frappe.PermissionError)

    def on_update(self):
        self.sync_user()
        clear_user_client_cache(self.user)

    def after_insert(self):
        audit.log("client_user.created", client=self.client, reference_doctype=self.doctype,
                  reference_name=self.name, details={"role": self.user_role})

    def on_trash(self):
        """Disable the Frappe user instead of deleting it (keeps the audit trail)."""
        if self.user and frappe.db.exists("User", self.user):
            frappe.db.set_value("User", self.user, "enabled", 0)
        clear_user_client_cache(self.user)
        audit.log("client_user.deleted", client=self.client, reference_doctype=self.doctype,
                  reference_name=self.name)

    # -- user provisioning -----------------------------------------------------
    def sync_user(self):
        user = self.get_or_create_user()
        self.sync_roles(user)
        self.sync_user_permission(user)
        if cint(user.enabled) != cint(self.enabled):
            user.db_set("enabled", cint(self.enabled))

    def get_or_create_user(self):
        if self.user and frappe.db.exists("User", self.user):
            user = frappe.get_doc("User", self.user)
        elif frappe.db.exists("User", self.email):
            user = frappe.get_doc("User", self.email)
        else:
            user = frappe.get_doc(
                {
                    "doctype": "User",
                    "email": self.email,
                    "first_name": self.first_name,
                    "middle_name": self.middle_name,
                    "last_name": self.last_name,
                    "mobile_no": self.phone,
                    "user_type": "System User",
                    "send_welcome_email": 0,
                    "enabled": cint(self.enabled),
                    # Random password: the Laravel system hard-coded "123456" for every
                    # staff member. Users receive a reset link instead.
                    "new_password": random_string(16),
                }
            )
            user.flags.ignore_permissions = True
            user.insert(ignore_permissions=True)

        if self.user != user.name:
            self.db_set("user", user.name, update_modified=False)
        return user

    def sync_roles(self, user):
        """Keep exactly one Kodafreight role on the linked user, leave other roles alone."""
        target = ROLE_MAP.get(self.user_role)
        current = {r.role for r in user.roles if r.role in ALL_CLIENT_ROLES}
        desired = {target} if (target and cint(self.enabled)) else set()
        if current == desired:
            return
        user.roles = [r for r in user.roles if r.role not in ALL_CLIENT_ROLES]
        for role in desired:
            user.append("roles", {"role": role})
        user.flags.ignore_permissions = True
        user.save(ignore_permissions=True)

    def sync_user_permission(self, user):
        """Tenant isolation layer 1: a User Permission pinned to this client."""
        existing = frappe.get_all(
            "User Permission",
            filters={"user": user.name, "allow": "Kodafreight Client"},
            fields=["name", "for_value"],
        )
        for row in existing:
            if row.for_value != self.client:
                frappe.delete_doc("User Permission", row.name, ignore_permissions=True,
                                  force=True)
        if not any(r.for_value == self.client for r in existing):
            frappe.get_doc(
                {
                    "doctype": "User Permission",
                    "user": user.name,
                    "allow": "Kodafreight Client",
                    "for_value": self.client,
                    "apply_to_all_doctypes": 1,
                }
            ).insert(ignore_permissions=True)

    # -- API -------------------------------------------------------------------
    @frappe.whitelist()
    def send_password_reset(self):
        if not self.user:
            frappe.throw(_("This record is not linked to a system user yet."))
        user = frappe.get_doc("User", self.user)
        user.reset_password(send_email=True)
        audit.log("client_user.password_reset", client=self.client,
                  reference_doctype=self.doctype, reference_name=self.name)
        return {"sent_to": self.user}

    @frappe.whitelist()
    def toggle_enabled(self):
        self.enabled = 0 if cint(self.enabled) else 1
        self.save()
        return {"enabled": self.enabled}


def make_staff_no(client):
    """Laravel parity: sequential staff number per client (`createStaffidNumber`)."""
    if not client:
        return None
    count = frappe.db.count("Kodafreight Client User", {"client": client})
    prefix = (client or "CLI").replace(" ", "")
    return f"{prefix}-STF-{count + 1:04d}"
