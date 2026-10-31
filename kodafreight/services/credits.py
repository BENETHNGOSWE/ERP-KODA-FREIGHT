"""SMS credit ledger.

Replaces the Laravel model where the quota was a single `customermessages.messageamount`
string and usage was `count(messages)` (which counted failed and pending messages and
ignored multi-part billing).

Every movement is written to `Kodafreight SMS Credit Transaction` and the running totals on
`Kodafreight SMS Credit Account` are updated inside the same transaction while holding a row
lock (`for_update=True`) so concurrent workers cannot oversell credits.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

TYPES_IN = ("Allocation", "Refund")
TYPES_OUT = ("Deduction", "Expiry")


class InsufficientCredits(frappe.ValidationError):
    pass


def get_account(client, create=True):
    """Return the credit account name for `client`, creating it when missing."""
    name = frappe.db.get_value("Kodafreight SMS Credit Account", {"client": client}, "name")
    if name:
        return name
    if not create:
        return None

    threshold = (
        frappe.db.get_value("Kodafreight Client", client, "low_balance_threshold")
        or frappe.db.get_single_value("Kodafreight Settings", "low_balance_threshold")
        or 0
    )
    doc = frappe.get_doc(
        {
            "doctype": "Kodafreight SMS Credit Account",
            "client": client,
            "allocated": 0,
            "used": 0,
            "balance": 0,
            "low_balance_threshold": threshold,
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    return doc.name


def get_balance(client) -> int:
    name = get_account(client, create=False)
    if not name:
        return 0
    return int(frappe.db.get_value("Kodafreight SMS Credit Account", name, "balance") or 0)


def get_summary(client) -> dict:
    name = get_account(client, create=False)
    if not name:
        return {"allocated": 0, "used": 0, "balance": 0, "low_balance_threshold": 0}
    row = frappe.db.get_value(
        "Kodafreight SMS Credit Account",
        name,
        ["allocated", "used", "balance", "low_balance_threshold", "expiry_date"],
        as_dict=True,
    )
    return {k: (int(v) if isinstance(v, (int, float)) else v) for k, v in (row or {}).items()}


def has_credits(client, quantity: int) -> bool:
    if not frappe.db.get_single_value("Kodafreight Settings", "enforce_credits"):
        return True
    account = get_account(client, create=False)
    if not account:
        return False
    row = frappe.db.get_value(
        "Kodafreight SMS Credit Account", account, ["balance", "allow_negative", "expiry_date"],
        as_dict=True,
    )
    if row.get("expiry_date") and str(row["expiry_date"]) < frappe.utils.today():
        return False
    if row.get("allow_negative"):
        return True
    return int(row.get("balance") or 0) >= int(quantity)


def post(client, transaction_type, quantity, reference_doctype=None, reference_name=None,
         remarks=None, enforce=True):
    """Post a credit movement. Returns the created transaction document.

    Raises InsufficientCredits when a Deduction would push the balance below zero and the
    account does not explicitly allow negative balances.
    """
    quantity = int(quantity or 0)
    if quantity <= 0:
        frappe.throw(_("Credit quantity must be a positive number."))
    if transaction_type not in TYPES_IN + TYPES_OUT + ("Adjustment",):
        frappe.throw(_("Invalid credit transaction type: {0}").format(transaction_type))

    account_name = get_account(client)

    # Lock the account row for the remainder of the database transaction.
    row = frappe.db.get_value(
        "Kodafreight SMS Credit Account",
        account_name,
        ["allocated", "used", "balance", "allow_negative"],
        as_dict=True,
        for_update=True,
    )
    allocated = int(row.get("allocated") or 0)
    used = int(row.get("used") or 0)
    balance = int(row.get("balance") or 0)
    allow_negative = bool(row.get("allow_negative"))

    if transaction_type == "Allocation":
        allocated += quantity
        balance += quantity
    elif transaction_type == "Refund":
        used = max(used - quantity, 0)
        balance += quantity
    elif transaction_type == "Deduction":
        enforce_credits = frappe.db.get_single_value("Kodafreight Settings", "enforce_credits")
        if enforce and enforce_credits and not allow_negative and balance < quantity:
            raise InsufficientCredits(
                _("Insufficient SMS credits for {0}: balance {1}, required {2}.").format(
                    client, balance, quantity
                )
            )
        used += quantity
        balance -= quantity
    elif transaction_type == "Expiry":
        balance = max(balance - quantity, 0)
    elif transaction_type == "Adjustment":
        # Positive adjustment; use Expiry/Deduction for negative corrections.
        allocated += quantity
        balance += quantity

    frappe.db.set_value(
        "Kodafreight SMS Credit Account",
        account_name,
        {
            "allocated": allocated,
            "used": used,
            "balance": balance,
            "last_transaction_on": now_datetime(),
        },
        update_modified=False,
    )
    frappe.db.set_value(
        "Kodafreight Client",
        client,
        {"credits_allocated": allocated, "credits_used": used, "credit_balance": balance},
        update_modified=False,
    )

    txn = frappe.get_doc(
        {
            "doctype": "Kodafreight SMS Credit Transaction",
            "client": client,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "balance_after": balance,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "remarks": remarks,
            "posting_datetime": now_datetime(),
        }
    )
    txn.flags.ignore_permissions = True
    txn.flags.from_ledger = True
    txn.insert(ignore_permissions=True)
    return txn


def allocate(client, quantity, remarks=None, reference_doctype=None, reference_name=None):
    return post(client, "Allocation", quantity, reference_doctype, reference_name, remarks)


def deduct(client, quantity, reference_doctype=None, reference_name=None, remarks=None,
           enforce=True):
    return post(client, "Deduction", quantity, reference_doctype, reference_name, remarks,
                enforce=enforce)


def refund(client, quantity, reference_doctype=None, reference_name=None, remarks=None):
    return post(client, "Refund", quantity, reference_doctype, reference_name, remarks)


def rebuild_account(client):
    """Recompute an account from its ledger (repair utility, used by tests and patches)."""
    account_name = get_account(client)
    rows = frappe.get_all(
        "Kodafreight SMS Credit Transaction",
        filters={"client": client},
        fields=["transaction_type", "quantity"],
        order_by="creation asc",
        limit_page_length=0,
    )
    allocated = used = 0
    for r in rows:
        q = int(r.quantity or 0)
        if r.transaction_type in ("Allocation", "Adjustment"):
            allocated += q
        elif r.transaction_type == "Deduction":
            used += q
        elif r.transaction_type == "Refund":
            used = max(used - q, 0)
        elif r.transaction_type == "Expiry":
            allocated = max(allocated - q, 0)
    balance = allocated - used
    frappe.db.set_value(
        "Kodafreight SMS Credit Account", account_name,
        {"allocated": allocated, "used": used, "balance": balance}, update_modified=False,
    )
    frappe.db.set_value(
        "Kodafreight Client", client,
        {"credits_allocated": allocated, "credits_used": used, "credit_balance": balance},
        update_modified=False,
    )
    return {"allocated": allocated, "used": used, "balance": balance}
