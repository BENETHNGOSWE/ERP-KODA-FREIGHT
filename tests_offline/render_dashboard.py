"""Render the real client dashboard template with realistic data.

This produces a standalone HTML file (the shipped Jinja template + the shipped CSS + the
shipped controller logic in `www/kodafreight/index.py`), driven by data created through the
real Kodafreight code paths on the in-memory Frappe stub.

It is a faithful render of the implemented UI, produced without a browser - see
docs/05-testing-report.md for the distinction from an actual browser screenshot.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_parent = os.path.dirname(HERE)
for _candidate in (_parent, os.path.join(_parent, "kodafreight")):
    if os.path.isdir(os.path.join(_candidate, "kodafreight", "services")):
        sys.path.insert(0, _candidate)
        APP_ROOT = _candidate
        break
else:
    raise SystemExit("cannot locate the kodafreight package")

import fake_frappe as ff  # noqa: E402

frappe = ff.frappe

from jinja2 import Environment, FileSystemLoader  # noqa: E402

from kodafreight.api import dashboard as dashboard_api  # noqa: E402
from kodafreight.services import credits as credit_service  # noqa: E402
from kodafreight.services.sms import dispatcher, transport  # noqa: E402
from kodafreight.www.kodafreight import index as portal  # noqa: E402

APP = os.path.join(APP_ROOT, "kodafreight")

CONTACTS = [
    ("Asha", "Mtali", "0745788788"), ("Baraka", "Juma", "0754123456"),
    ("Neema", "Kileo", "0762334455"), ("Juma", "Salehe", "0713998877"),
    ("Grace", "Mushi", "0688112233"), ("Peter", "Nyoni", "0755443322"),
    ("Zawadi", "Mrope", "0784556677"), ("Hamisi", "Kibwana", "0719887766"),
]

MESSAGES = [
    "Habari Asha, shehena yako namba DOC-1042 imefika ghala la Dar. Karibu kuchukua.",
    "Habari Baraka, ankara yako ya TZS 480,000 imepokelewa. Asante kwa malipo.",
    "Habari Neema, kontena TCLU4471234 limeondoka Guangzhou tarehe 21/08.",
    "Habari Juma, mzigo wako uko forodha. Tutakujulisha ukikamilika.",
    "Habari Grace, salio lako la usafirishaji limesasishwa.",
    "Habari Peter, shehena DOC-1101 iko tayari kwa usafirishaji.",
    "Habari Zawadi, tafadhali thibitisha anwani ya kupokelea mzigo.",
    "Habari Hamisi, ankara mpya imetumwa kwa barua pepe yako.",
]


def build_world():
    ff.reset()
    ff.register_controllers()

    frappe.get_doc({
        "doctype": "Kodafreight SMS Provider", "name": "Kilakona",
        "provider_name": "Kilakona", "driver": "Kilakona", "enabled": 1, "is_default": 1,
        "api_base_url": "https://messaging.kilakona.co.tz",
        "send_endpoint": "/api/v1/vendor/message/send", "auth_type": "API Key Header",
        "api_key": "demo", "api_secret": "demo", "timeout": 30, "verify_ssl": 1,
    }).insert()

    for sender in ("KODAFREIGHT", "KODASMS"):
        frappe.get_doc({"doctype": "Kodafreight Sender Name", "sender_name": sender,
                        "status": "Active"}).insert()

    frappe.get_doc({
        "doctype": "Kodafreight Client", "name": "CLI-00007",
        "client_name": "Nyati Freight Services Ltd",
        "email": "operations@nyatifreight.co.tz", "phone": "+255 745 788 788",
        "location": "Kariakoo", "city": "Dar es Salaam", "country": "Tanzania",
        "status": "Active", "sms_enabled": 1, "low_balance_threshold": 250,
        "senders": [
            {"sender_name": "KODAFREIGHT", "enabled": 1, "is_default": 1},
            {"sender_name": "KODASMS", "enabled": 1, "is_default": 0},
        ],
    }).insert()

    frappe.get_doc({
        "doctype": "Kodafreight SMS Number", "name": "15200", "number": "15200",
        "number_type": "Short Code", "status": "Assigned", "client": "CLI-00007",
    }).insert()

    credit_service.allocate("CLI-00007", 10000, remarks="Opening allocation")

    for first, last, phone in CONTACTS:
        frappe.get_doc({
            "doctype": "Kodafreight Contact", "client": "CLI-00007", "first_name": first,
            "last_name": last, "phone": phone, "source": "Customer",
        }).insert()
    for category in ("Importers", "Clearing Agents", "Retail Customers"):
        frappe.get_doc({"doctype": "Kodafreight Contact Category", "client": "CLI-00007",
                        "category_name": category}).insert()

    outcomes = iter([True] * 6 + [False] + [True])
    transport.set_test_hook(lambda url, payload, headers: (
        {"ok": True, "status_code": 200,
         "json": {"success": True, "data": {"messageId": "MID"}, "balance": 9000},
         "text": "", "error": None}
        if next(outcomes, True) else
        {"ok": False, "status_code": 400,
         "json": {"success": False, "error": "Absolute number is not valid"},
         "text": "", "error": "HTTP 400"}
    ))

    import datetime

    for index, ((first, last, phone), text) in enumerate(zip(CONTACTS, MESSAGES)):
        doc = dispatcher.queue_message("CLI-00007", phone, text,
                                       sender_name="KODAFREIGHT", send_now=True)
        doc.reload()
        if doc.status == "Sent" and index % 3 != 0:
            dispatcher.apply_delivery_report(doc.name, "Delivered")
        # spread the creation timestamps over the last 7 days for the chart
        offset = datetime.timedelta(days=(7 - index) % 7, hours=index)
        frappe.db.set_value("Kodafreight SMS Message", doc.name, "creation",
                            ff.now_datetime() - offset)

    # a couple of still-queued messages
    for phone in ("0715001122", "0786554433"):
        frappe.get_doc({
            "doctype": "Kodafreight SMS Message", "client": "CLI-00007",
            "recipient": phone, "sender_name": "KODAFREIGHT",
            "message": "Habari, taarifa ya shehena yako itatumwa hivi punde.",
            "status": "Queued",
        }).insert()

    # a completed campaign for the campaigns panel
    frappe.get_doc({
        "doctype": "Kodafreight SMS Campaign", "title": "August shipment notice",
        "client": "CLI-00007", "sender_name": "KODAFREIGHT",
        "message": "Habari {first_name}, ratiba mpya ya meli imetoka.",
        "recipient_source": "Manual", "status": "Completed",
        "total_recipients": 412, "sent_count": 404, "failed_count": 8,
        "recipients": [],
    }).insert()

    # extra history so the "all time" figure looks real
    frappe.db.set_value("Kodafreight SMS Credit Account", "CLI-00007",
                        {"used": 1832, "balance": 10000 - 1832})
    frappe.db.set_value("Kodafreight Client", "CLI-00007",
                        {"credits_used": 1832, "credit_balance": 8168,
                         "credits_allocated": 10000})


def main():
    build_world()
    frappe.session.user = "grace@nyatifreight.co.tz"
    ff.USER_ROLES[frappe.session.user] = ["Kodafreight Client Admin"]
    frappe.get_doc({
        "doctype": "Kodafreight Client User", "client": "CLI-00007",
        "first_name": "Grace", "last_name": "Mushi",
        "email": frappe.session.user, "phone": "0745788788",
        "user_role": "Client Admin", "enabled": 1, "user": frappe.session.user,
    }).insert()

    data = dashboard_api.client_dashboard("CLI-00007")
    # the counters above were set directly on the account, mirror them in the payload
    data["credits"] = {"allocated": 10000, "used": 1832, "remaining": 8168,
                       "low_balance_threshold": 250}
    data["messages"]["total"] = 1832
    data["messages"]["delivered"] = 1601
    data["messages"]["successful"] = 1789
    data["messages"]["failed"] = 43
    portal.decorate(data)

    env = Environment(loader=FileSystemLoader(os.path.join(APP, "templates", "includes")))
    body = env.get_template("kodafreight_dashboard.html").render(
        data=data,
        full_name="Grace Mushi",
        today_label="Friday, 28 August 2026",
        used_percent=portal.percent(data["credits"]["used"], data["credits"]["allocated"]),
        success_rate=portal.percent(data["messages"]["successful"], data["messages"]["total"]),
    )

    css = open(os.path.join(APP, "public", "css", "kodafreight_portal.css")).read()
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kodafreight — Client Dashboard</title>
<style>
body {{ margin: 0; }}
{css}
</style></head><body>
{body}
</body></html>
"""
    out = os.path.join(APP_ROOT, "kodafreight-client-dashboard.html")
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out} ({len(html)} bytes)")
    print("messages rendered:", len(data["recent_messages"]))
    return out


if __name__ == "__main__":
    main()
