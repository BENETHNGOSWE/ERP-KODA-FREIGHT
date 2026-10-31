"""Audit logging - the Laravel application had none."""

import json

import frappe


def log(action, client=None, reference_doctype=None, reference_name=None, status="Success",
        details=None):
    """Write a `Kodafreight Audit Log` entry. Never raises into the caller."""
    try:
        if not frappe.db.get_single_value("Kodafreight Settings", "enable_audit_log"):
            return None
    except Exception:
        return None

    try:
        ip = None
        try:
            ip = frappe.local.request_ip
        except Exception:
            ip = None

        doc = frappe.get_doc(
            {
                "doctype": "Kodafreight Audit Log",
                "action": action,
                "client": client,
                "user": frappe.session.user,
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "status": status,
                "ip_address": ip,
                "details": json.dumps(details, default=str, indent=1) if details else None,
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
        return doc.name
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Kodafreight audit log failure")
        return None
