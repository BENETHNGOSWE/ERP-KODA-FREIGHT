"""Provider factory - resolves a `Kodafreight SMS Provider` document into a driver."""

from .kilakona import KilakonaProvider
from .messaging_service_tz import MessagingServiceTZProvider
from .base import BaseProvider, SendResult  # noqa: F401  (re-export)

DRIVERS = {
    "Kilakona": KilakonaProvider,
    "Messaging Service TZ": MessagingServiceTZProvider,
    "Generic HTTP": KilakonaProvider,  # same JSON contract, different base URL
}


def build_driver(settings: dict) -> BaseProvider:
    """Build a driver from a plain settings dict (no database access - unit testable)."""
    driver_name = (settings or {}).get("driver") or "Kilakona"
    driver_class = DRIVERS.get(driver_name)
    if not driver_class:
        raise ValueError(f"Unknown SMS driver: {driver_name}")
    return driver_class(settings)


def provider_settings(provider_doc) -> dict:
    """Convert a `Kodafreight SMS Provider` document into a driver settings dict."""
    secret = None
    try:
        secret = provider_doc.get_password("api_secret", raise_exception=False)
    except Exception:
        secret = provider_doc.get("api_secret")

    return {
        "driver": provider_doc.driver,
        "api_base_url": provider_doc.api_base_url,
        "send_endpoint": provider_doc.send_endpoint,
        "balance_endpoint": provider_doc.balance_endpoint,
        "auth_type": provider_doc.auth_type,
        "api_key": provider_doc.api_key,
        "api_secret": secret,
        "timeout": provider_doc.timeout or 30,
        "verify_ssl": bool(provider_doc.verify_ssl),
        "max_recipients_per_request": provider_doc.max_recipients_per_request or 100,
    }


def get_driver(provider_name=None):
    """Resolve the provider to use and return (driver, provider_name)."""
    import frappe

    if not provider_name:
        provider_name = frappe.db.get_single_value("Kodafreight Settings", "default_provider")
    if not provider_name:
        provider_name = frappe.db.get_value(
            "Kodafreight SMS Provider", {"is_default": 1, "enabled": 1}, "name"
        )
    if not provider_name:
        provider_name = frappe.db.get_value("Kodafreight SMS Provider", {"enabled": 1}, "name")
    if not provider_name:
        frappe.throw(frappe._("No enabled SMS Provider is configured."))

    doc = frappe.get_cached_doc("Kodafreight SMS Provider", provider_name)
    if not doc.enabled:
        frappe.throw(frappe._("SMS Provider {0} is disabled.").format(provider_name))

    return build_driver(provider_settings(doc)), provider_name
