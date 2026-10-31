"""Container - the consolidation unit (Laravel `Container` + `Containerloading`).

A container is the manifest: which cargo loads are inside it, how much CBM that is, and
how full the box is. Submitting a container stamps the load rows and enforces the two
rules the Laravel system never checked:

  * a load can only be in ONE container at a time
  * the manifest cannot exceed the container's declared capacity
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from kodafreight.services import freight
from kodafreight.utils.tenant import get_user_client, is_platform_admin


class KodafreightContainer(Document):
    def validate(self):
        self._enforce_tenant()
        self._validate_loads()
        self._compute_totals()

    def _enforce_tenant(self):
        user = frappe.session.user
        own = get_user_client(user)
        if own:
            if self.client and self.client != own:
                frappe.throw(
                    _("You cannot manage a container for another client."),
                    frappe.PermissionError,
                )
            self.client = own
        elif not is_platform_admin(user):
            frappe.throw(
                _("Your user is not linked to a Kodafreight client."), frappe.PermissionError
            )

    def _validate_loads(self):
        seen = set()
        for row in self.loads or []:
            if not row.cargo_load:
                continue
            if row.cargo_load in seen:
                frappe.throw(_("Cargo load {0} is listed twice.").format(row.cargo_load))
            seen.add(row.cargo_load)

            load_client, load_docstatus = frappe.db.get_value(
                "Kodafreight Cargo Load", row.cargo_load, ["client", "docstatus"]
            ) or (None, None)
            if load_client != self.client:
                frappe.throw(
                    _("Cargo load {0} belongs to another client.").format(row.cargo_load),
                    frappe.PermissionError,
                )
            if load_docstatus != 1:
                frappe.throw(
                    _("Cargo load {0} must be submitted before it can be loaded.").format(
                        row.cargo_load
                    )
                )
            other = frappe.db.get_value(
                "Kodafreight Cargo Load", row.cargo_load, "container"
            )
            if other and other != self.name:
                frappe.throw(
                    _("Cargo load {0} is already in container {1}.").format(
                        row.cargo_load, other
                    )
                )

    def _compute_totals(self):
        total_cbm = sum(flt(row.cbm) for row in self.loads or [])
        total_weight = sum(flt(row.weight) for row in self.loads or [])
        capacity = flt(self.max_volume_cbm)

        self.load_count = len(self.loads or [])
        self.total_cbm = total_cbm
        self.total_weight = total_weight
        self.fill_percent = (total_cbm / capacity * 100) if capacity else 0

        if capacity and total_cbm > capacity:
            frappe.throw(
                _(
                    "Manifest is {0} CBM but the container only holds {1} CBM. Remove a load "
                    "or raise the capacity."
                ).format(round(total_cbm, 4), capacity)
            )

    def on_submit(self):
        for row in self.loads or []:
            frappe.db.set_value(
                "Kodafreight Cargo Load",
                row.cargo_load,
                {"container": self.name, "shipment_status": "In Container"},
                update_modified=False,
            )
        self.db_set("status", "Loading", update_modified=False)

    def on_cancel(self):
        for row in self.loads or []:
            frappe.db.set_value(
                "Kodafreight Cargo Load",
                row.cargo_load,
                {"container": None, "shipment_status": "Registered"},
                update_modified=False,
            )

    @frappe.whitelist()
    def mark_shipped(self, etd=None):
        self.check_permission("write")
        self.db_set("status", "Shipped", update_modified=False)
        if etd:
            self.db_set("etd", etd, update_modified=False)
        freight.set_load_status(
            [row.cargo_load for row in self.loads or []], "Shipped"
        )
        return "Shipped"

    @frappe.whitelist()
    def mark_arrived(self):
        self.check_permission("write")
        self.db_set("status", "Arrived", update_modified=False)
        freight.set_load_status(
            [row.cargo_load for row in self.loads or []], "Arrived at Godown"
        )
        return "Arrived"
