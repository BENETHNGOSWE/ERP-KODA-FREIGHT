"""HTTP transport used by the SMS drivers.

Isolated in its own module so it can be stubbed in unit tests without patching `requests`
globally, and so every provider call has the same timeout / TLS / error semantics.
"""

import json as _json

_TEST_HOOK = None  # tests may set this to a callable(url, payload, headers) -> dict


def set_test_hook(fn):
    """Install a stub used instead of a real HTTP request (unit tests only)."""
    global _TEST_HOOK
    _TEST_HOOK = fn


def post_json(url, payload, headers=None, timeout=30, verify=True) -> dict:
    """POST `payload` as JSON.

    Returns a plain dict so drivers never depend on the HTTP client:
        {ok: bool, status_code: int|None, json: dict|None, text: str, error: str|None}
    Network problems never raise - they are reported as ok=False with an error message,
    which the dispatcher treats as a retryable failure.
    """
    if _TEST_HOOK is not None:
        return _TEST_HOOK(url, payload, headers)

    try:
        import requests
    except ImportError:  # pragma: no cover - requests ships with Frappe
        return {"ok": False, "status_code": None, "json": None, "text": "",
                "error": "python-requests is not installed"}

    try:
        response = requests.post(
            url,
            data=_json.dumps(payload),
            headers=headers or {"Content-Type": "application/json"},
            timeout=timeout,
            verify=verify,
        )
    except Exception as exc:
        return {"ok": False, "status_code": None, "json": None, "text": "",
                "error": f"{type(exc).__name__}: {exc}"}

    body = None
    try:
        body = response.json()
    except Exception:
        body = None

    return {
        "ok": 200 <= response.status_code < 300,
        "status_code": response.status_code,
        "json": body,
        "text": (response.text or "")[:2000],
        "error": None if 200 <= response.status_code < 300 else f"HTTP {response.status_code}",
    }
