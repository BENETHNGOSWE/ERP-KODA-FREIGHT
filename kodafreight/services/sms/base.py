"""Provider driver contract.

Every driver receives an already validated payload and returns a `SendResult`.
Drivers never touch the database, never deduct credits and never raise for a provider
level failure - they translate the provider answer into a uniform structure so the
dispatcher can apply the business rules in one place.
"""

from dataclasses import dataclass, field
from typing import Optional

import json


@dataclass
class SendResult:
    success: bool
    message: str = ""
    provider_message_id: Optional[str] = None
    balance: Optional[int] = None
    raw: dict = field(default_factory=dict)
    status_code: Optional[int] = None
    retryable: bool = False

    def as_json(self) -> str:
        try:
            return json.dumps(self.raw, default=str, indent=1)[:9000]
        except Exception:  # pragma: no cover - defensive
            return str(self.raw)[:9000]


class BaseProvider:
    """Base class for SMS drivers."""

    name = "base"

    def __init__(self, settings: dict):
        # settings: api_base_url, send_endpoint, api_key, api_secret, timeout, verify_ssl, ...
        self.settings = settings or {}

    # -- helpers ---------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return (self.settings.get("api_base_url") or "").rstrip("/")

    @property
    def timeout(self) -> int:
        return int(self.settings.get("timeout") or 30)

    @property
    def verify_ssl(self) -> bool:
        return bool(self.settings.get("verify_ssl", True))

    def endpoint(self, path_key: str, default: str = "") -> str:
        path = self.settings.get(path_key) or default
        if path and not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    # -- contract --------------------------------------------------------------
    def send(self, recipients, message, sender_name, reference=None) -> SendResult:
        raise NotImplementedError

    def balance(self):
        return None

    def parse_callback(self, payload: dict) -> dict:
        """Normalise a delivery report into
        {provider_message_id, reference, status, raw_status, error}."""
        raise NotImplementedError
