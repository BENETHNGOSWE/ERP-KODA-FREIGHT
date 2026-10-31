"""Kilakona driver - the provider used by the legacy Laravel application.

Reverse engineered from `app/Helpers/ControllerHelper.php`:

    POST https://messaging.kilakona.co.tz/api/v1/vendor/message/send
    Headers: api_key: <SMS_API_KEY2>, api_secret: <SMS_API_SECRET2>, Content-Type: application/json
    Body:    {"senderId": "...", "messageType": "text", "message": "...", "contacts": "2557...,2557..."}
    Success: {"success": true, ...}
    Failure: {"success": false, "error": "..."}

Differences from the Laravel implementation (deliberate fixes):
  * TLS verification is ON by default (Laravel used CURLOPT_SSL_VERIFYPEER = false).
  * An explicit timeout is applied (Laravel had none - a hung provider blocked a web worker).
  * A `reference` idempotency key is sent so duplicated retries can be reconciled.
  * Transport/5xx errors are marked retryable; 4xx/business errors are not.
"""

from .base import BaseProvider, SendResult


class KilakonaProvider(BaseProvider):
    name = "Kilakona"

    DEFAULT_BASE_URL = "https://messaging.kilakona.co.tz"
    DEFAULT_SEND_PATH = "/api/v1/vendor/message/send"

    def headers(self) -> dict:
        return {
            "api_key": self.settings.get("api_key") or "",
            "api_secret": self.settings.get("api_secret") or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def build_payload(self, recipients, message, sender_name, reference=None) -> dict:
        if isinstance(recipients, (list, tuple, set)):
            contacts = ",".join(str(r).strip() for r in recipients if str(r).strip())
        else:
            contacts = str(recipients).strip()

        payload = {
            "senderId": sender_name,
            "messageType": "text",
            "message": message,
            "contacts": contacts,
        }
        if reference:
            payload["reference"] = reference
        return payload

    def send(self, recipients, message, sender_name, reference=None) -> SendResult:
        from .transport import post_json

        url = self.endpoint("send_endpoint", self.DEFAULT_SEND_PATH)
        payload = self.build_payload(recipients, message, sender_name, reference)

        response = post_json(
            url,
            payload,
            headers=self.headers(),
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        return self.parse_response(response)

    # -- response handling ---------------------------------------------------------
    def parse_response(self, response: dict) -> SendResult:
        """`response` = {ok, status_code, json, text, error} produced by transport.post_json."""
        if not response.get("ok") and response.get("json") is None:
            return SendResult(
                success=False,
                message=response.get("error") or "Provider request failed",
                raw=response,
                status_code=response.get("status_code"),
                retryable=True,
            )

        body = response.get("json") or {}
        status_code = response.get("status_code")
        success = bool(body.get("success"))

        # Some Kilakona deployments answer {"statusCode": 200, "message": "..."} instead.
        if not success and str(body.get("statusCode") or "") in ("200", "0"):
            success = True

        if success:
            data = body.get("data") or {}
            message_id = None
            if isinstance(data, dict):
                message_id = data.get("messageId") or data.get("id") or data.get("reference")
            elif isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    message_id = first.get("messageId") or first.get("id")
            return SendResult(
                success=True,
                message=body.get("message") or "Message sent successfully!",
                provider_message_id=message_id,
                balance=_as_int(body.get("balance") or body.get("credits")),
                raw=body,
                status_code=status_code,
            )

        error = body.get("error") or body.get("message") or "Unknown error"
        retryable = bool(status_code and int(status_code) >= 500)
        return SendResult(
            success=False,
            message=f"Failed to send message: {error}",
            raw=body,
            status_code=status_code,
            retryable=retryable,
        )

    def parse_callback(self, payload: dict) -> dict:
        payload = payload or {}
        raw_status = str(
            payload.get("status")
            or payload.get("deliveryStatus")
            or payload.get("messageStatus")
            or ""
        ).strip()
        return {
            "provider_message_id": payload.get("messageId")
            or payload.get("message_id")
            or payload.get("id"),
            "reference": payload.get("reference") or payload.get("clientReference"),
            "recipient": payload.get("msisdn") or payload.get("contact") or payload.get("to"),
            "status": map_status(raw_status),
            "raw_status": raw_status,
            "error": payload.get("error") or payload.get("reason"),
        }


DELIVERED = {"delivered", "deliveredtohandset", "success", "delivrd", "completed"}
FAILED = {"failed", "undelivered", "rejected", "expired", "undeliv", "error", "blacklisted"}
SENT = {"sent", "submitted", "accepted", "pending", "enroute", "queued"}


def map_status(raw_status: str) -> str:
    key = (raw_status or "").strip().lower().replace("_", "").replace(" ", "")
    if key in DELIVERED:
        return "Delivered"
    if key in FAILED:
        return "Failed"
    if key in SENT:
        return "Sent"
    return ""


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
