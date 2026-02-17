from __future__ import annotations

from typing import Any


REDACT_KEYS = {"apikey", "apiKey", "secret", "passphrase", "private_key", "authorization"}


def build_user_subscribe_payload(api_key: str, secret: str, passphrase: str) -> dict[str, Any]:
    return {
        "type": "subscribe",
        "channel": "user",
        "auth": {
            "apikey": api_key,
            "secret": secret,
            "passphrase": passphrase,
        },
    }


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def _redact(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                if k in REDACT_KEYS:
                    out[k] = "***REDACTED***"
                else:
                    out[k] = _redact(v)
            return out
        if isinstance(obj, list):
            return [_redact(x) for x in obj]
        return obj

    return _redact(payload)
