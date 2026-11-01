"""Static validation of the Kodafreight app package.

Checks that can be made without a bench, and that catch the classes of error which
otherwise only surface during `bench migrate`:

  * every DocType JSON parses and has the required keys
  * every Link/Table field points at a DocType that exists (in this app or in Frappe core)
  * child tables are marked istable and are only referenced by Table fields
  * `options` of Select fields do not contain values the Python code never handles
  * every dotted path referenced from hooks.py / workspace / reports actually resolves
  * every controller file defines the class Frappe expects for its DocType
  * no secret-looking literals are committed
"""

import ast
import importlib.util
import json
import os
import pathlib
import re
import sys

_here = pathlib.Path(__file__).resolve().parent
for _cand in (_here.parent, _here.parent / "kodafreight"):
    if (_cand / "kodafreight" / "services").is_dir():
        APP = _cand
        break
else:
    raise SystemExit("cannot locate the kodafreight package")
PKG = APP / "kodafreight"
DOCTYPE_DIR = PKG / "kodafreight" / "doctype"

CORE_DOCTYPES = {
    "User", "Role", "File", "Company", "Contact", "Address", "Currency", "Country",
    "User Permission", "DocType", "Report", "Workspace", "Number Card", "Dashboard Chart",
    # ERPNext doctypes the freight module integrates with (required_apps = erpnext).
    "Customer", "Supplier", "Item", "Item Group", "UOM", "Warehouse", "Sales Invoice",
    "Sales Invoice Item", "Payment Entry", "Payment Entry Reference", "Mode of Payment",
    "Account", "Cost Center", "Letter Head", "Bank Account", "Branch",
}

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def snake(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# ---------------------------------------------------------------- doctypes
doctypes = {}
for folder in sorted(DOCTYPE_DIR.iterdir()):
    if not folder.is_dir() or folder.name.startswith((".", "__")):
        continue
    jf = folder / f"{folder.name}.json"
    if not jf.exists():
        err(f"missing JSON for doctype folder {folder.name}")
        continue
    try:
        data = json.loads(jf.read_text())
    except json.JSONDecodeError as exc:
        err(f"{jf}: invalid JSON: {exc}")
        continue
    doctypes[data["name"]] = (data, folder)
    if snake(data["name"]) != folder.name:
        err(f"{data['name']}: folder name {folder.name} != {snake(data['name'])}")
    if not (folder / "__init__.py").exists():
        err(f"{folder}: missing __init__.py")

known = set(doctypes) | CORE_DOCTYPES

for name, (data, folder) in doctypes.items():
    for key in ("doctype", "module", "name", "fields", "permissions", "field_order"):
        if key not in data:
            err(f"{name}: missing key '{key}'")
    if data.get("module") != "Kodafreight":
        err(f"{name}: wrong module {data.get('module')}")
    if data.get("engine") != "InnoDB":
        err(f"{name}: engine must be InnoDB")

    fieldnames = [f["fieldname"] for f in data["fields"]]
    if len(fieldnames) != len(set(fieldnames)):
        dupes = {f for f in fieldnames if fieldnames.count(f) > 1}
        err(f"{name}: duplicate fieldnames {dupes}")
    if data["field_order"] != fieldnames:
        err(f"{name}: field_order does not match fields")

    for field in data["fields"]:
        ftype = field["fieldtype"]
        if ftype in ("Link", "Table", "Table MultiSelect"):
            target = field.get("options")
            if not target:
                err(f"{name}.{field['fieldname']}: {ftype} without options")
            elif target not in known:
                err(f"{name}.{field['fieldname']}: links to unknown DocType '{target}'")
            elif ftype == "Table":
                child = doctypes.get(target)
                if child and not child[0].get("istable"):
                    err(f"{name}.{field['fieldname']}: '{target}' is not a child table")
            elif ftype == "Link":
                child = doctypes.get(target)
                if child and child[0].get("istable"):
                    err(f"{name}.{field['fieldname']}: Link points at child table '{target}'")
        if ftype == "Select" and field.get("default") and field.get("options"):
            options = [o for o in str(field["options"]).split("\n")]
            if field["default"] not in options:
                err(f"{name}.{field['fieldname']}: default '{field['default']}' "
                    f"not among options")
        if field.get("reqd") and field.get("hidden") and not field.get("default"):
            warn(f"{name}.{field['fieldname']}: hidden + mandatory without default")

    autoname = data.get("autoname") or ""
    if autoname.startswith("field:"):
        target = autoname.split(":", 1)[1]
        if target not in fieldnames:
            err(f"{name}: autoname field '{target}' does not exist")
        else:
            fld = next(f for f in data["fields"] if f["fieldname"] == target)
            if not fld.get("reqd"):
                err(f"{name}: autoname field '{target}' must be mandatory")
    if autoname == "naming_series:" and "naming_series" not in fieldnames:
        err(f"{name}: naming_series autoname without a naming_series field")

    if data.get("istable"):
        if data.get("permissions"):
            err(f"{name}: child table must not define permissions")
    else:
        roles = {p["role"] for p in data.get("permissions", [])}
        if "Kodafreight Platform Admin" not in roles:
            err(f"{name}: platform admin permission missing")

    # controller class
    controller = folder / f"{folder.name}.py"
    if controller.exists():
        tree = ast.parse(controller.read_text())
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        expected = "".join(
            w.upper() if w.lower() == "sms" else w[:1].upper() + w[1:]
            for w in name.split()
        )
        if expected not in classes:
            err(f"{name}: controller class '{expected}' not found (found {classes})")

# ---------------------------------------------------------------- python references
def resolve(dotted):
    """Return True when a dotted path exists inside the package (module or attribute)."""
    parts = dotted.split(".")
    if parts[0] != "kodafreight":
        return True
    for split in range(len(parts), 1, -1):
        module_path = PKG.parent / pathlib.Path(*parts[:split]).with_suffix(".py")
        pkg_init = PKG.parent / pathlib.Path(*parts[:split]) / "__init__.py"
        path = module_path if module_path.exists() else (pkg_init if pkg_init.exists() else None)
        if not path:
            continue
        attrs = parts[split:]
        if not attrs:
            return True
        source = ast.parse(path.read_text())
        names = set()
        for node in source.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    names.add(a.asname or a.name.split(".")[0])
        return attrs[0] in names
    return False


hooks_src = (PKG / "hooks.py").read_text()
for dotted in sorted(set(re.findall(r"\"(kodafreight\.[a-zA-Z0-9_.]+)\"", hooks_src))):
    if not resolve(dotted):
        err(f"hooks.py references missing path: {dotted}")

# doctype JS / py whitelisted method references from JS
for folder in DOCTYPE_DIR.iterdir():
    if not folder.is_dir():
        continue
    js = folder / f"{folder.name}.js"
    if not js.exists():
        continue
    py = folder / f"{folder.name}.py"
    src = py.read_text() if py.exists() else ""
    for call in set(re.findall(r"frm\.call\(\s*\"([a-z_]+)\"", js.read_text())):
        if f"def {call}(" not in src:
            err(f"{folder.name}.js calls frm.call('{call}') but the controller has no such "
                f"method")
    for query in set(re.findall(r"query:\s*\"([a-zA-Z0-9_.]+)\"", js.read_text())):
        if not resolve(query):
            err(f"{folder.name}.js references missing query method {query}")

# ---------------------------------------------------------------- workspace & reports
ws = json.loads((PKG / "kodafreight" / "workspace" / "kodafreight" / "kodafreight.json").read_text())
for link in ws["links"]:
    if link["type"] != "Link":
        continue
    if link["link_type"] == "DocType" and link["link_to"] not in known:
        err(f"workspace links to unknown DocType {link['link_to']}")
    if link["link_type"] == "Report":
        folder = PKG / "kodafreight" / "report" / snake(link["link_to"])
        if not folder.exists():
            err(f"workspace links to missing report {link['link_to']}")

for card in ws.get("number_cards", []):
    folder = PKG / "kodafreight" / "number_card" / snake(card["number_card_name"])
    if not folder.exists():
        err(f"workspace references missing number card {card['number_card_name']}")
for chart in ws.get("charts", []):
    folder = PKG / "kodafreight" / "dashboard_chart" / snake(chart["chart_name"])
    if not folder.exists():
        err(f"workspace references missing chart {chart['chart_name']}")

content = json.loads(ws["content"])
for block in content:
    data = block.get("data", {})
    if block["type"] == "number_card":
        if not (PKG / "kodafreight" / "number_card" / snake(data["number_card_name"])).exists():
            err(f"workspace content references missing card {data['number_card_name']}")
    if block["type"] == "card":
        labels = {link["label"] for link in ws["links"] if link["type"] == "Card Break"}
        if data["card_name"] not in labels:
            err(f"workspace content references missing card break {data['card_name']}")

for folder in (PKG / "kodafreight" / "report").iterdir():
    if not folder.is_dir() or folder.name.startswith((".", "__")):
        continue
    jf = folder / f"{folder.name}.json"
    pf = folder / f"{folder.name}.py"
    if not jf.exists():
        err(f"report {folder.name}: missing JSON")
        continue
    meta = json.loads(jf.read_text())
    if meta["report_type"] == "Script Report" and not pf.exists():
        err(f"report {meta['name']}: script report without a python file")
    if pf.exists() and "def execute(" not in pf.read_text():
        err(f"report {meta['name']}: execute() missing")
    if meta.get("ref_doctype") not in known:
        err(f"report {meta['name']}: unknown ref_doctype {meta.get('ref_doctype')}")

# ---------------------------------------------------------------- python syntax + secrets
SECRET_PATTERNS = [
    (re.compile(r"VBGBW1HNk3tBYT0nSqu0"), "legacy Laravel API secret"),
    (re.compile(r"Beneth@\d+"), "legacy password"),
    (re.compile(r"api_secret\s*=\s*[\"'][A-Za-z0-9]{12,}[\"']"), "hard-coded api secret"),
    (re.compile(r"password\s*=\s*[\"'](?!\{)[^\"']{6,}[\"']"), "hard-coded password"),
]
for path in PKG.rglob("*"):
    if path.suffix not in (".py", ".js", ".json", ".html", ".css", ".txt", ".md"):
        continue
    text = path.read_text(errors="ignore")
    if path.suffix == ".py":
        try:
            ast.parse(text)
        except SyntaxError as exc:
            err(f"{path}: syntax error {exc}")
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(text):
            err(f"{path}: possible committed secret ({label})")

for required in ("hooks.py", "modules.txt", "patches.txt", "install.py", "permissions.py",
                 "__init__.py"):
    if not (PKG / required).exists():
        err(f"missing required file kodafreight/{required}")
if (PKG / "modules.txt").read_text().strip() != "Kodafreight":
    err("modules.txt must contain exactly 'Kodafreight'")
if "__version__" not in (PKG / "__init__.py").read_text():
    err("__init__.py must define __version__")

# ------------------------------------------- structural packaging checks
# Regression guard for the bug that broke `bench install-app`:
# a package directory without __init__.py imports as a namespace package whose
# __file__ is None, and frappe.model.sync does os.path.dirname(None).
_bad_pkgs = set()
for path in sorted(APP.rglob("*.py")):
    rel = path.relative_to(APP)
    if any(part in {".git", "__pycache__", "tests_offline"} for part in rel.parts):
        continue
    for parent in [path.parent, *path.parent.parents]:
        if parent == APP or APP not in parent.parents:
            break
        if not (parent / "__init__.py").exists():
            _bad_pkgs.add(parent.relative_to(APP))
            break
for _p in sorted(_bad_pkgs):
    err(f"package dir without __init__.py: {_p}")

hooks_src = (PKG / "hooks.py").read_text()
for folder, dt in (("number_card", "Number Card"), ("dashboard_chart", "Dashboard Chart")):
    if (PKG / "kodafreight" / folder).is_dir() and dt not in hooks_src:
        err(f"{folder}/ present but hooks.importable_doctypes lacks '{dt}'")

ws = PKG / "kodafreight" / "workspace" / "kodafreight" / "kodafreight.json"
if ws.exists():
    import json as _json
    _ws = _json.loads(ws.read_text())
    if _ws.get("app") != "kodafreight":
        err("workspace/kodafreight.json must set app = 'kodafreight'")

# ---------------------------------------------------------------- report
print(f"DocTypes validated : {len(doctypes)}")
print(f"Warnings           : {len(warnings)}")
for w in warnings:
    print(f"  WARN  {w}")
print(f"Errors             : {len(errors)}")
for e in errors:
    print(f"  ERROR {e}")
sys.exit(1 if errors else 0)
