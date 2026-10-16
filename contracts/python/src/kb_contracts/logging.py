"""C13: structured JSON log schema and redaction. Secrets never reach logs (SEC-03)."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"
# Any key containing one of these fragments is redacted (so client_secret, x-auth-token,
# database_url, refresh_token ... are all caught). Bare "auth" is deliberately absent: it would
# also hide legitimate fields such as "author".
SENSITIVE_FRAGMENTS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "private_key",
    "credential",
    "database_url",
    "dsn",
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
_KV = re.compile(
    r"(?i)([\"']?(?:token|password|passwd|secret|api[_-]?key)[\"']?\s*[=:]\s*)([\"']?)([^\s,;&\"'}]+)"
)
_DSN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/@]+:)([^\s@/]+)(@)")


class Redactor:
    """Redacts known-sensitive keys, bearer tokens, and any registered literal secret."""

    def __init__(self) -> None:
        self._literals: set[str] = set()

    def register_secret(self, value: str) -> None:
        if len(value) >= 8:
            self._literals.add(value)

    def scrub_text(self, text: str) -> str:
        for lit in self._literals:
            text = text.replace(lit, REDACTED)
        text = _BEARER.sub(f"Bearer {REDACTED}", text)
        text = _DSN.sub(rf"\1{REDACTED}\3", text)
        return _KV.sub(rf"\1\2{REDACTED}", text)

    def scrub(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: REDACTED if _is_sensitive_key(k) else self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub(v) for v in obj]
        if isinstance(obj, str):
            return self.scrub_text(obj)
        return obj


def _is_sensitive_key(key: object) -> bool:
    k = str(key).lower()
    return any(f in k for f in SENSITIVE_FRAGMENTS)


REDACTOR = Redactor()


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts (UTC), level, logger, msg, correlation_id, extras."""

    def __init__(self, redactor: Redactor = REDACTOR) -> None:
        super().__init__()
        self._r = redactor

    def format(self, record: logging.LogRecord) -> str:
        extras = {
            k: v for k, v in record.__dict__.items() if k not in _STD and not k.startswith("_")
        }
        body: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **extras,
        }
        if record.exc_info:
            body["exc"] = self.formatException(record.exc_info)
        return json.dumps(self._r.scrub(body), default=str)


_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def configure_logging(level: str = "INFO", redactor: Redactor = REDACTOR) -> None:
    handler = logging.StreamHandler()  # stderr: stdout is reserved for the READY handshake
    handler.setFormatter(JsonFormatter(redactor))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
