"""Optional data migration from the legacy Laravel (PostgreSQL) database.

Not required to install or run Kodafreight 0.1. Use it when you want to carry the existing
tenants, staff, sender names, quotas, contacts and message history across.

Step 1 - export the source tables to JSON (on the Laravel server):

    psql -d freight -At -c "select json_build_object(
        'clients', (select coalesce(json_agg(t), '[]') from clients t),
        'client_admins', (select coalesce(json_agg(t), '[]') from client_admins t),
        'staffs', (select coalesce(json_agg(t), '[]') from staffs t),
        'users', (select coalesce(json_agg(t), '[]') from users t),
        'sendernames', (select coalesce(json_agg(t), '[]') from sendernames t),
        'client_sendername', (select coalesce(json_agg(t), '[]') from client_sendername t),
        'clientactivenames', (select coalesce(json_agg(t), '[]') from clientactivenames t),
        'customermessages', (select coalesce(json_agg(t), '[]') from customermessages t),
        'customercategories', (select coalesce(json_agg(t), '[]') from customercategories t),
        'customers', (select coalesce(json_agg(t), '[]') from customers t),
        'customerleads', (select coalesce(json_agg(t), '[]') from customerleads t),
        'messages', (select coalesce(json_agg(t), '[]') from messages t),
        'client_types', (select coalesce(json_agg(t), '[]') from client_types t))" > laravel.json

Step 2 - import on the Frappe side:

    bench --site <site> execute kodafreight.utils.migrate_from_laravel.run \
        --kwargs "{'path': '/path/laravel.json', 'dry_run': False}"

The import is idempotent: every created document stores the source primary key in
`legacy_id`, and existing records are skipped.
"""

import json

import frappe

from kodafreight.services import credits as credit_service
from kodafreight.services.sms import phone as phone_service

STATUS_MAP = {"pending": "Queued", "delivered": "Sent", "failed": "Failed"}
ROLE_MAP = {"admin": "Client Admin", "kodasmspackage": "SMS Manager"}


def run(path, dry_run=True, limit_messages=None):
    with open(path) as fh:
        data = json.load(fh)
    return import_data(data, dry_run=dry_run, limit_messages=limit_messages)


def import_data(data, dry_run=True, limit_messages=None):
    stats = {k: 0 for k in (
        "client_types", "clients", "senders", "assignments", "credits", "users",
        "categories", "contacts", "messages", "skipped")}
    client_map = {}
    category_map = {}

    for row in data.get("client_types", []):
        if not frappe.db.exists("Kodafreight Client Type", row["name"]):
            _insert({"doctype": "Kodafreight Client Type", "type_name": row["name"],
                     "legacy_id": str(row["id"])}, dry_run)
            stats["client_types"] += 1

    for row in data.get("clients", []):
        existing = frappe.db.get_value("Kodafreight Client", {"legacy_id": str(row["id"])}, "name")
        if existing:
            client_map[row["id"]] = existing
            stats["skipped"] += 1
            continue
        doc = _insert({
            "doctype": "Kodafreight Client",
            "client_name": row["client_name"],
            "email": row.get("client_email") or f"client{row['id']}@example.invalid",
            "phone": row.get("client_phonenumber") or "0000000000",
            "location": row.get("client_location") or "-",
            "address_line_1": row.get("client_address_one"),
            "address_line_2": row.get("client_address_two"),
            "city": row.get("client_city") or "-",
            "country": row.get("client_country") or "Tanzania",
            "zipcode": row.get("client_zipcode"),
            "tin_number": row.get("client_tinnumber"),
            "fax": row.get("client_fax"),
            "website": row.get("client_website_link"),
            "status": "Active",  # the legacy schema has no status column
            "legacy_id": str(row["id"]),
        }, dry_run)
        client_map[row["id"]] = doc
        stats["clients"] += 1

    for row in data.get("sendernames", []):
        if not frappe.db.exists("Kodafreight Sender Name", row["name"]):
            _insert({"doctype": "Kodafreight Sender Name", "sender_name": row["name"],
                     "status": "Active", "legacy_id": str(row["id"])}, dry_run)
            stats["senders"] += 1

    sender_by_id = {r["id"]: r["name"] for r in data.get("sendernames", [])}
    active = {r["client_id"]: r["sendername_id"] for r in data.get("clientactivenames", [])}
    assignments = {}
    for row in data.get("client_sendername", []):
        assignments.setdefault(row["client_id"], []).append(row)

    for legacy_client, rows in assignments.items():
        client = client_map.get(legacy_client)
        if not client or dry_run:
            continue
        doc = frappe.get_doc("Kodafreight Client", client)
        existing = {r.sender_name for r in doc.senders}
        for row in rows:
            sender = sender_by_id.get(row["sendername_id"])
            if not sender or sender in existing:
                continue
            doc.append("senders", {
                "sender_name": sender,
                "enabled": 1,
                "is_default": 1 if active.get(legacy_client) == row["sendername_id"]
                else int(row.get("is_default") or 0),
            })
            stats["assignments"] += 1
        doc.save(ignore_permissions=True)

    for row in data.get("customermessages", []):
        client = client_map.get(row["client_id"])
        if not client or dry_run:
            continue
        try:
            quantity = int(float(row.get("messageamount") or 0))
        except (TypeError, ValueError):
            quantity = 0
        if quantity <= 0:
            continue
        if frappe.db.exists("Kodafreight SMS Credit Transaction",
                            {"client": client, "remarks": f"Laravel quota #{row['id']}"}):
            continue
        credit_service.allocate(client, quantity, remarks=f"Laravel quota #{row['id']}")
        stats["credits"] += 1

    users_by_id = {u["id"]: u for u in data.get("users", [])}
    for row in data.get("client_admins", []):
        client = client_map.get(row["client_id"])
        if not client:
            continue
        email = (row.get("email") or "").lower()
        if not email or frappe.db.exists("Kodafreight Client User", email):
            stats["skipped"] += 1
            continue
        _insert({
            "doctype": "Kodafreight Client User",
            "client": client,
            "first_name": row.get("firstname") or "User",
            "middle_name": row.get("middlename"),
            "last_name": row.get("lastname") or ".",
            "email": email,
            "phone": row.get("phone") or "0000000000",
            "user_role": "Client Admin",
            "enabled": 1,
            "legacy_id": str(row["id"]),
        }, dry_run)
        stats["users"] += 1

    for row in data.get("staffs", []):
        client = client_map.get(row["client_id"])
        email = (row.get("staff_email") or "").lower()
        if not client or not email or frappe.db.exists("Kodafreight Client User", email):
            continue
        legacy_user = users_by_id.get(row.get("user_id")) or {}
        _insert({
            "doctype": "Kodafreight Client User",
            "client": client,
            "first_name": row.get("staff_firstname") or "Staff",
            "middle_name": row.get("staff_middlename"),
            "last_name": row.get("staff_lastname") or ".",
            "email": email,
            "phone": row.get("phone") or "0000000000",
            "gender": (row.get("staff_gender") or "").capitalize() or None,
            "address": row.get("staff_address"),
            "bio": row.get("staff_bio"),
            "staff_no": row.get("staff_no"),
            "user_role": ROLE_MAP.get(legacy_user.get("user_type"), "Client Staff"),
            "enabled": 0 if row.get("deleted_at") else 1,
            "legacy_id": str(row["id"]),
        }, dry_run)
        stats["users"] += 1

    for row in data.get("customercategories", []):
        client = client_map.get(row["client_id"])
        if not client:
            continue
        existing = frappe.db.get_value(
            "Kodafreight Contact Category",
            {"client": client, "category_name": row["categoryname"]}, "name")
        if existing:
            category_map[row["id"]] = existing
            continue
        category_map[row["id"]] = _insert({
            "doctype": "Kodafreight Contact Category",
            "client": client,
            "category_name": row["categoryname"],
            "description": row.get("categorydescription"),
            "legacy_id": str(row["id"]),
        }, dry_run)
        stats["categories"] += 1

    for source, rows in (("Customer", data.get("customers", [])),
                         ("Lead", data.get("customerleads", []))):
        for row in rows:
            client = client_map.get(row["client_id"])
            if not client:
                continue
            msisdn = phone_service.try_normalise(row.get("phone"))
            if not msisdn:
                stats["skipped"] += 1
                continue
            if frappe.db.exists("Kodafreight Contact", {"client": client, "msisdn": msisdn}):
                stats["skipped"] += 1
                continue
            _insert({
                "doctype": "Kodafreight Contact",
                "client": client,
                "first_name": row.get("customer_firstname") or "Contact",
                "last_name": row.get("customer_lastname"),
                "phone": msisdn,
                "email": row.get("customer_email"),
                "city": row.get("customer_city"),
                "country": row.get("customer_country"),
                "category": category_map.get(row.get("category_id")),
                "source": source,
                "legacy_id": f"{source}:{row['id']}",
            }, dry_run)
            stats["contacts"] += 1

    messages = data.get("messages", [])
    if limit_messages:
        messages = messages[: int(limit_messages)]
    for row in messages:
        client = client_map.get(row["client_id"])
        if not client:
            continue
        if frappe.db.exists("Kodafreight SMS Message", {"legacy_id": str(row["id"])}):
            continue
        msisdn = phone_service.try_normalise(row.get("customer_number")) or \
            row.get("customer_number")
        sender = row.get("sender_id") or frappe.db.get_value(
            "Kodafreight Client", client, "default_sender_name")
        if not dry_run and sender and not frappe.db.exists("Kodafreight Sender Name", sender):
            frappe.get_doc({"doctype": "Kodafreight Sender Name", "sender_name": sender,
                            "status": "Inactive"}).insert(ignore_permissions=True)
        _insert({
            "doctype": "Kodafreight SMS Message",
            "client": client,
            "recipient": msisdn,
            "sender_name": sender,
            "message": row.get("content") or "",
            "status": STATUS_MAP.get(row.get("status"), "Failed"),
            "creation": row.get("created_at"),
            "legacy_id": str(row["id"]),
        }, dry_run, historical=True)
        stats["messages"] += 1

    if not dry_run:
        frappe.db.commit()
    return stats


def _insert(payload, dry_run, historical=False):
    if dry_run:
        return payload.get("client_name") or payload.get("email") or "DRY-RUN"
    doc = frappe.get_doc(payload)
    doc.flags.ignore_permissions = True
    if historical:
        # Historical rows must not be re-dispatched or re-charged.
        doc.flags.ignore_validate = True
        doc.flags.ignore_mandatory = True
    doc.insert(ignore_permissions=True, ignore_if_duplicate=True)
    return doc.name
