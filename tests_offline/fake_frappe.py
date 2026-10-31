"""A very small in-memory stand-in for the parts of the Frappe framework that Kodafreight's
business logic touches.

Why: a full Frappe/ERPNext v16 bench (MariaDB + Redis + Node + wkhtmltopdf) cannot be
provisioned inside this sandbox, but the credit ledger, tenant guards, provider drivers and
dispatcher must still be executed - not just eyeballed. This stub runs the *real* Kodafreight
modules against an in-memory document store so their behaviour is genuinely tested.

It is a test harness only; it is not shipped inside the application package.
"""

import sys
import types
from datetime import date, datetime, timedelta

# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


class _Dict(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            return None

    def __setattr__(self, key, value):
        self[key] = value


class ValidationError(Exception):
    pass


class PermissionError_(Exception):
    pass


class DoesNotExistError(Exception):
    pass


class RateLimitExceededError(Exception):
    pass


class AuthenticationError(Exception):
    pass


CONTROLLERS = {}
STORE = {}
COUNTERS = {}
SINGLES = {}


def reset():
    STORE.clear()
    COUNTERS.clear()
    SINGLES.clear()
    SINGLES["Kodafreight Settings"] = {
        "doctype": "Kodafreight Settings",
        "default_provider": "Kilakona",
        "default_country_code": "255",
        "national_number_length": 9,
        "enforce_credits": 1,
        "allow_negative_balance": 0,
        "charge_per_part": 1,
        "queue_batch_size": 20,
        "max_retries": 3,
        "retry_backoff_minutes": 5,
        "request_timeout": 30,
        "verify_ssl": 1,
        "dispatch_enabled": 1,
        "webhook_secret": "test-secret",
        "webhook_ip_allowlist": "",
        "api_rate_limit_per_minute": 60,
        "enable_audit_log": 1,
        "low_balance_threshold": 100,
    }
    frappe.session.user = "Administrator"
    _CACHE.clear()


# --------------------------------------------------------------------------------------
# document
# --------------------------------------------------------------------------------------


class Document:
    def __init__(self, data=None):
        data = dict(data or {})
        object.__setattr__(self, "_data", data)
        object.__setattr__(self, "flags", _Dict())
        object.__setattr__(self, "_before_save", None)

    # dict-ish access -----------------------------------------------------------
    def __getattr__(self, item):
        data = object.__getattribute__(self, "_data")
        if item in data:
            value = data[item]
            if isinstance(value, list):
                return [Document(v) if isinstance(v, dict) else v for v in value]
            return value
        return None

    def __setattr__(self, key, value):
        if key in ("flags", "_data", "_before_save"):
            object.__setattr__(self, key, value)
        else:
            self._data[key] = value

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def as_dict(self):
        return dict(self._data)

    def append(self, field, row):
        self._data.setdefault(field, []).append(dict(row))
        return Document(self._data[field][-1])

    # persistence ---------------------------------------------------------------
    def _run(self, hook):
        fn = getattr(self, hook, None)
        if callable(fn):
            fn()

    def insert(self, ignore_permissions=False, ignore_if_duplicate=False):
        doctype = self._data["doctype"]
        if not self._data.get("name"):
            self._data["name"] = _make_name(doctype, self._data)
        self._data.setdefault("creation", now_datetime())
        self._data.setdefault("modified", now_datetime())
        self._data.setdefault("owner", frappe.session.user)
        if not self.flags.ignore_validate:
            self._run("before_insert")
            self._run("validate")
        STORE.setdefault(doctype, {})[self._data["name"]] = self._data
        if not self.flags.ignore_validate:
            self._run("after_insert")
            self._run("on_update")
        return self

    def save(self, ignore_permissions=False):
        doctype = self._data["doctype"]
        if object.__getattribute__(self, "_before_save") is None:
            before = STORE.get(doctype, {}).get(self._data.get("name"))
            object.__setattr__(self, "_before_save",
                               Document(dict(before)) if before else None)
        self._run("validate")
        self._data["modified"] = now_datetime()
        STORE.setdefault(doctype, {})[self._data["name"]] = self._data
        self._run("on_update")
        return self

    def db_update(self):
        doctype = self._data.get("doctype")
        name = self._data.get("name")
        if not doctype or not name:
            return self  # child row: mutating the dict is enough
        STORE.setdefault(doctype, {})[name] = self._data
        return self

    def is_new(self):
        doctype = self._data.get("doctype")
        name = self._data.get("name")
        return not (doctype and name and name in STORE.get(doctype, {}))

    def db_set(self, field, value=None, update_modified=True):
        if isinstance(field, dict):
            self._data.update(field)
        else:
            self._data[field] = value
        return self.db_update()

    def reload(self):
        import copy

        stored = STORE.get(self._data["doctype"], {}).get(self._data["name"])
        if stored:
            object.__setattr__(self, "_data", stored)
            object.__setattr__(self, "_before_save", Document(copy.deepcopy(stored)))
        return self

    def delete(self):
        STORE.get(self._data["doctype"], {}).pop(self._data.get("name"), None)

    def has_value_changed(self, field):
        before = object.__getattribute__(self, "_before_save")
        if not before:
            return True
        return before.get(field) != self._data.get(field)

    def get_doc_before_save(self):
        return object.__getattribute__(self, "_before_save")

    def get_password(self, field, raise_exception=True):
        return self._data.get(field)

    def only_for(self, *args, **kwargs):
        return True


def _make_name(doctype, data):
    if doctype == "Kodafreight SMS Credit Account":
        return data["client"]
    if doctype in ("Kodafreight Sender Name",):
        return data["sender_name"]
    if doctype == "Kodafreight Client User":
        return data["email"]
    COUNTERS[doctype] = COUNTERS.get(doctype, 0) + 1
    prefix = "".join(w[0] for w in doctype.split()[-2:]).upper()
    return f"{prefix}-{COUNTERS[doctype]:05d}"


# --------------------------------------------------------------------------------------
# query helpers
# --------------------------------------------------------------------------------------


def _match(row, filters):
    if not filters:
        return True
    if isinstance(filters, str):
        return row.get("name") == filters
    if isinstance(filters, list):
        filters = {f[0]: f[1] for f in filters}
    for key, condition in filters.items():
        value = row.get(key)
        if isinstance(condition, (list, tuple)):
            op, target = condition
            op = op.lower()
            if op == "!=" and value == target:
                return False
            if op == "=" and value != target:
                return False
            if op == "in" and value not in target:
                return False
            if op == "not in" and value in target:
                return False
            if op == "like":
                pattern = str(target).replace("%", "")
                if pattern not in str(value or ""):
                    return False
            if op == "<" and not (value is not None and str(value) < str(target)):
                return False
            if op == ">" and not (value is not None and str(value) > str(target)):
                return False
            if op == "is":
                if target == "not set" and value:
                    return False
                if target == "set" and not value:
                    return False
        else:
            if value != condition:
                return False
    return True


class _DB:
    def get_value(self, doctype, filters=None, fieldname="name", as_dict=False,
                  for_update=False, order_by=None, **kwargs):
        rows = [r for r in STORE.get(doctype, {}).values() if _match(r, filters)]
        if order_by and "desc" in str(order_by):
            rows = list(reversed(rows))
        if not rows:
            if as_dict:
                return None
            if isinstance(fieldname, (list, tuple)):
                return None
            return None
        row = rows[0]
        if as_dict:
            if isinstance(fieldname, (list, tuple)):
                return _Dict({f: row.get(f) for f in fieldname})
            return _Dict(row)
        if isinstance(fieldname, (list, tuple)):
            return tuple(row.get(f) for f in fieldname)
        return row.get(fieldname)

    def set_value(self, doctype, filters, field, value=None, update_modified=True):
        rows = [r for r in STORE.get(doctype, {}).values() if _match(r, filters)]
        for row in rows:
            if isinstance(field, dict):
                row.update(field)
            else:
                row[field] = value
        return len(rows)

    def exists(self, doctype, filters=None):
        if isinstance(filters, str) or filters is None:
            return filters in STORE.get(doctype, {})
        rows = [r for r in STORE.get(doctype, {}).values() if _match(r, filters)]
        return rows[0]["name"] if rows else None

    def count(self, doctype, filters=None):
        return len([r for r in STORE.get(doctype, {}).values() if _match(r, filters)])

    def get_single_value(self, doctype, field):
        return SINGLES.get(doctype, {}).get(field)

    def sql(self, query, values=None, as_dict=False):
        q = " ".join(str(query).split())
        # emulate the atomic claim used by the dispatcher
        if "SET status = 'Sending'" in q and values:
            name = values.get("name") if isinstance(values, dict) else values[0]
            row = STORE.get("Kodafreight SMS Message", {}).get(name)
            if row and row.get("status") == "Queued":
                row["status"] = "Sending"
                self._rowcount = 1
            else:
                self._rowcount = 0
            return []
        if "Client is no longer active" in q and values:
            client = values[0] if isinstance(values, tuple) else values
            count = 0
            for row in STORE.get("Kodafreight SMS Message", {}).values():
                if row.get("client") == client and row.get("status") in ("Queued", "Draft"):
                    row["status"] = "Cancelled"
                    row["error_message"] = "Client is no longer active"
                    count += 1
            self._rowcount = count
            return []
        if "SET status = 'Cancelled'" in q and values:
            campaign = values[0] if isinstance(values, tuple) else values
            count = 0
            for row in STORE.get("Kodafreight SMS Message", {}).values():
                if row.get("campaign") == campaign and row.get("status") == "Queued":
                    row["status"] = "Cancelled"
                    count += 1
            self._rowcount = count
            return []
        if "count(*) as total" in q and "tabKodafreight SMS Message" in q:
            client = (values or {}).get("client") if isinstance(values, dict) else None
            rows = [r for r in STORE.get("Kodafreight SMS Message", {}).values()
                    if not client or r.get("client") == client]
            today_str = today()
            return [_Dict({
                "total": len(rows),
                "successful": len([r for r in rows if r.get("status") in ("Sent", "Delivered")]),
                "delivered": len([r for r in rows if r.get("status") == "Delivered"]),
                "failed": len([r for r in rows if r.get("status") == "Failed"]),
                "pending": len([r for r in rows if r.get("status") in
                                ("Queued", "Sending", "Draft")]),
                "today_count": len([r for r in rows
                                    if str(r.get("creation"))[:10] == today_str]),
                "credits": sum(cint(r.get("credits_used")) for r in rows),
            })]
        if "group by date(creation)" in q:
            client = (values or {}).get("client") if isinstance(values, dict) else None
            buckets = {}
            for r in STORE.get("Kodafreight SMS Message", {}).values():
                if client and r.get("client") != client:
                    continue
                day = str(r.get("creation"))[:10]
                b = buckets.setdefault(day, {"day": day, "sent": 0, "failed": 0})
                if r.get("status") in ("Sent", "Delivered"):
                    b["sent"] += 1
                elif r.get("status") == "Failed":
                    b["failed"] += 1
            return [_Dict(b) for b in sorted(buckets.values(), key=lambda x: x["day"])]
        if "coalesce(sum(allocated),0)" in q:
            rows = STORE.get("Kodafreight SMS Credit Account", {}).values()
            return [_Dict({"allocated": sum(cint(r.get("allocated")) for r in rows),
                           "used": sum(cint(r.get("used")) for r in rows),
                           "balance": sum(cint(r.get("balance")) for r in rows)})]
        self._rowcount = 0
        return []

    @property
    def _cursor(self):
        return _Dict({"rowcount": getattr(self, "_rowcount", 0)})

    def commit(self):
        pass

    def rollback(self):
        pass

    def escape(self, value):
        return "'" + str(value).replace("'", "''") + "'"


_CACHE = {}


class _Cache:
    def hget(self, name, key):
        return _CACHE.get((name, key))

    def hset(self, name, key, value):
        _CACHE[(name, key)] = value

    def hdel(self, name, key):
        _CACHE.pop((name, key), None)

    def delete_key(self, name):
        for key in [k for k in _CACHE if k[0] == name]:
            _CACHE.pop(key)

    def get_value(self, key):
        return _CACHE.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        _CACHE[key] = value


# --------------------------------------------------------------------------------------
# frappe module surface
# --------------------------------------------------------------------------------------


def _translate(text):
    return text


def throw(msg, exc=ValidationError, title=None):
    raise exc(str(msg))


def msgprint(msg, *args, **kwargs):
    pass


def get_doc(*args, **kwargs):
    if args and isinstance(args[0], dict):
        data = dict(args[0])
        cls = CONTROLLERS.get(data.get("doctype"), Document)
        return cls(data)
    if kwargs and not args:
        data = dict(kwargs)
        cls = CONTROLLERS.get(data.get("doctype"), Document)
        return cls(data)
    doctype, name = args[0], args[1]
    row = STORE.get(doctype, {}).get(name)
    if row is None:
        raise DoesNotExistError(f"{doctype} {name} not found")
    cls = CONTROLLERS.get(doctype, Document)
    doc = cls(row)
    import copy

    object.__setattr__(doc, "_before_save", Document(copy.deepcopy(row)))
    return doc


def get_cached_doc(*args, **kwargs):
    if args and args[0] in SINGLES and len(args) == 1:
        return Document(SINGLES[args[0]])
    return get_doc(*args, **kwargs)


def get_single(doctype):
    return Document(SINGLES.setdefault(doctype, {"doctype": doctype}))


def get_all(doctype, filters=None, fields=None, or_filters=None, order_by=None,
            limit_page_length=None, start=0, pluck=None, **kwargs):
    rows = [r for r in STORE.get(doctype, {}).values() if _match(r, filters)]
    if order_by:
        field = str(order_by).split()[0].strip("`")
        reverse = "desc" in str(order_by).lower()
        rows.sort(key=lambda r: str(r.get(field) or ""), reverse=reverse)
    if start:
        rows = rows[int(start):]
    if limit_page_length:
        rows = rows[: int(limit_page_length)]
    if pluck:
        return [r.get(pluck) for r in rows]
    if fields:
        return [_Dict({f: r.get(f) for f in fields + ["name"]}) for r in rows]
    return [_Dict(r) for r in rows]


def get_roles(user=None):
    return USER_ROLES.get(user or frappe.session.user, [])


def only_for(*roles):
    user_roles = set(get_roles())
    if frappe.session.user == "Administrator":
        return True
    if not user_roles & set(roles if isinstance(roles, (list, tuple, set)) else [roles]):
        raise PermissionError_(f"Not permitted: requires {roles}")
    return True


def has_permission(doctype, ptype="read", doc=None, throw=False, user=None):
    return True


def log_error(message=None, title=None):
    return None


def get_traceback():
    import traceback

    return traceback.format_exc()


def enqueue(method, queue=None, timeout=None, enqueue_after_commit=False, **kwargs):
    """Run the job synchronously (the sandbox has no worker)."""
    if isinstance(method, str):
        module_path, fn_name = method.rsplit(".", 1)
        import importlib

        module = importlib.import_module(module_path)
        fn = getattr(module, fn_name)
    else:
        fn = method
    return fn(**kwargs)


def generate_hash(length=32):
    import uuid

    return uuid.uuid4().hex[:length]


def publish_realtime(*args, **kwargs):
    return None


def delete_doc(doctype, name, **kwargs):
    STORE.get(doctype, {}).pop(name, None)


def clear_cache(*args, **kwargs):
    return None


def validate_and_sanitize_search_inputs(fn):
    return fn


def whitelist(*dargs, **dkwargs):
    def decorator(fn):
        return fn

    if dargs and callable(dargs[0]):
        return dargs[0]
    return decorator


# ---- utils -----------------------------------------------------------------------


def now_datetime():
    return datetime.now()


def today():
    return date.today().isoformat()


def getdate(value=None):
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def add_to_date(dt, minutes=0, days=0, **kwargs):
    dt = dt or now_datetime()
    return dt + timedelta(minutes=minutes, days=days)


def add_days(dt, days):
    return (getdate(dt) + timedelta(days=days)).isoformat()


def add_months(dt, months):
    return add_days(dt, 30 * months)


def cint(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def flt(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def random_string(length=16):
    return generate_hash(length)


def get_fullname(user=None):
    return user or "Test User"


def pretty_date(value):
    return "just now"


def format_datetime(value, fmt=None):
    return str(value)


# --------------------------------------------------------------------------------------
# assemble the fake package
# --------------------------------------------------------------------------------------

frappe = types.ModuleType("frappe")
frappe._ = _translate
frappe._dict = _Dict
frappe.throw = throw
frappe.msgprint = msgprint
frappe.get_doc = get_doc
frappe.get_cached_doc = get_cached_doc
frappe.get_single = get_single
frappe.get_all = get_all
# NOTE: this stub does not implement Frappe's permission_query_conditions / User Permission
# filtering, so `get_list` behaves like `get_all` here. Documented limitation: an isolation
# test that relies on frappe.get_list filtering cannot be validated offline. Verified on a
# real Frappe 16 bench (see docs/05-testing-report.md).
frappe.get_list = get_all
frappe.get_roles = get_roles
frappe.only_for = only_for
frappe.has_permission = has_permission
frappe.log_error = log_error
frappe.get_traceback = get_traceback
frappe.enqueue = enqueue
frappe.generate_hash = generate_hash
frappe.publish_realtime = publish_realtime
frappe.delete_doc = delete_doc
frappe.clear_cache = clear_cache
frappe.whitelist = whitelist
frappe.validate_and_sanitize_search_inputs = validate_and_sanitize_search_inputs
frappe.ValidationError = ValidationError
frappe.PermissionError = PermissionError_
frappe.DoesNotExistError = DoesNotExistError
frappe.RateLimitExceededError = RateLimitExceededError
frappe.AuthenticationError = AuthenticationError
frappe.db = _DB()
frappe.cache = lambda: _Cache()
frappe.session = _Dict({"user": "Administrator"})
frappe.local = _Dict({"request_ip": "127.0.0.1"})
frappe.flags = _Dict()

utils_mod = types.ModuleType("frappe.utils")
utils_mod.now_datetime = now_datetime
utils_mod.today = today
utils_mod.getdate = getdate
utils_mod.add_to_date = add_to_date
utils_mod.add_days = add_days
utils_mod.add_months = add_months
utils_mod.cint = cint
utils_mod.flt = flt
utils_mod.random_string = random_string
utils_mod.get_fullname = get_fullname
utils_mod.pretty_date = pretty_date
utils_mod.format_datetime = format_datetime
password_mod = types.ModuleType("frappe.utils.password")
password_mod.get_decrypted_password = lambda *a, **k: SINGLES.get(
    "Kodafreight Settings", {}).get("webhook_secret")
utils_mod.password = password_mod
frappe.utils = utils_mod

model_mod = types.ModuleType("frappe.model")
document_mod = types.ModuleType("frappe.model.document")
document_mod.Document = Document
model_mod.document = document_mod
frappe.model = model_mod

USER_ROLES = {"Administrator": ["Administrator", "System Manager"]}

sys.modules["frappe"] = frappe
sys.modules["frappe.utils"] = utils_mod
sys.modules["frappe.utils.password"] = password_mod
sys.modules["frappe.model"] = model_mod
sys.modules["frappe.model.document"] = document_mod


def register_controllers():
    """Bind the real Kodafreight controllers to their DocTypes."""
    from kodafreight.kodafreight.doctype.kodafreight_client.kodafreight_client import (
        KodafreightClient,
    )
    from kodafreight.kodafreight.doctype.kodafreight_contact.kodafreight_contact import (
        KodafreightContact,
    )
    from kodafreight.kodafreight.doctype.kodafreight_sms_campaign.kodafreight_sms_campaign import (
        KodafreightSMSCampaign,
    )
    from kodafreight.kodafreight.doctype.kodafreight_sms_credit_account.kodafreight_sms_credit_account import (  # noqa: E501
        KodafreightSMSCreditAccount,
    )
    from kodafreight.kodafreight.doctype.kodafreight_sms_message.kodafreight_sms_message import (
        KodafreightSMSMessage,
    )
    from kodafreight.kodafreight.doctype.kodafreight_sender_name.kodafreight_sender_name import (
        KodafreightSenderName,
    )

    CONTROLLERS.update({
        "Kodafreight Client": KodafreightClient,
        "Kodafreight SMS Message": KodafreightSMSMessage,
        "Kodafreight SMS Campaign": KodafreightSMSCampaign,
        "Kodafreight SMS Credit Account": KodafreightSMSCreditAccount,
        "Kodafreight Sender Name": KodafreightSenderName,
        "Kodafreight Contact": KodafreightContact,
    })
