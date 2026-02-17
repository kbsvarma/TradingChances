from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

from polymarket_arb.wss_auth import REDACT_KEYS


class RedactionFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        secrets = [
            os.getenv("PRIVATE_KEY", ""),
            os.getenv("CLOB_API_KEY", ""),
            os.getenv("CLOB_API_SECRET", ""),
            os.getenv("CLOB_API_PASSPHRASE", ""),
        ]
        self.secret_values = [s for s in secrets if s]
        key_pattern = "|".join(re.escape(x) for x in REDACT_KEYS)
        self.field_regex = re.compile(rf'(?i)("?(?:{key_pattern})"?\s*[:=]\s*)("?[^",\s]+"?)')

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        msg = self.field_regex.sub(r"\1***REDACTED***", msg)
        for secret in self.secret_values:
            msg = msg.replace(secret, "***REDACTED***")
        record.msg = msg
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in ("correlation_id", "market_id", "token_id", "event_type"):
            val = getattr(record, field, None)
            if val is not None:
                payload[field] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(handler)
