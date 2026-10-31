"""messaging-service.co.tz driver (NextSMS style broker).

Kept because the legacy Laravel code base still contains this integration
(commented-out helpers plus the recorded request/response in `sms_log.txt`):

    {"auth": {"clientId": "...", "clientSecret": "..."},
     "messages": [{"text": "...", "msisdn": "2557...", "source": "KODASMS",
                   "reference": "MSG_...", "coding": "GSM7"}]}
    -> {"status": 1, "message": "Successfully submitted 1 message(s) to broker.",
        "data": null, "balance": 10214}
"""

from .base import BaseProvider, SendResult
from .encoding import analyse
from .kilakona import map_status, _as_int


class MessagingServiceTZProvider(BaseProvider):
    name = "Messaging Service TZ"

    DEFAULT_BASE_URL = "https://messaging-service.co.tz"
    DEFAULT_SEND_PATH = "/api/sms/v1/text/multi"

    def headers(self) -> dict:
        return {"Content-Type": "application/json", "Accept": "application/json"}

    def build_payload(self, recipients, message, sender_name, reference=None) -> dict:
        if not isinstance(recipients, (list, tuple, set)):
            recipients = [recipients]
        coding = analyse(message)["encoding"]
        messages = []
        for index, msisdn in enumerate(r for r in recipients if str(r).strip()):
            messages.append(
                {
                    "text": message,
                    "msisdn": str(msisdn).strip(),
                    "source": sender_name,
                    "reference": f"{reference}_{index}" if reference else None,
                    "coding": coding,
                }
            )
        return {
            "auth": {
                "clientId": self.settings.get("api_key") or "",
                "clientSecret": self.settings.get("api_secret") or "",
            },
            "messages": messages,
        }

    def send(self, recipients, message, sender_name, reference=None) -> SendResult:
        from .transport import post_json

        url = self.endpoint("send_endpoint", self.DEFAULT_SEND_PATH)
        response = post_json(
            url,
            self.build_payload(recipients, message, sender_name, reference),
            headers=self.headers(),
            timeout=self.timeout,
            verify=self.verify_ssl,
        )
        return self.parse_response(response)

    def parse_response(self, response: dict) -> SendResult:
        if not response.get("ok") and response.get("json") is None:
            return SendResult(
                success=False,
                message=response.get("error") or "Provider request failed",
                raw=response,
                status_code=response.get("status_code"),
                retryable=True,
            )
        body = response.get("json") or {}
        status = str(body.get("status") or "")
        success = status in ("1", "0000", "success", "True", "true")
        if success:
            return SendResult(
                success=True,
                message=body.get("message") or "Submitted to broker",
                balance=_as_int(body.get("balance")),
                raw=body,
                status_code=response.get("status_code"),
            )
        return SendResult(
            success=False,
            message=body.get("message") or "Broker rejected the message",
            raw=body,
            status_code=response.get("status_code"),
            retryable=bool(response.get("status_code") and int(response["status_code"]) >= 500),
        )

    def parse_callback(self, payload: dict) -> dict:
        payload = payload or {}
        raw_status = str(payload.get("status") or payload.get("dlr_status") or "")
        return {
            "provider_message_id": payload.get("messageId") or payload.get("id"),
            "reference": payload.get("reference"),
            "recipient": payload.get("msisdn") or payload.get("destination"),
            "status": map_status(raw_status),
            "raw_status": raw_status,
            "error": payload.get("error"),
        }
