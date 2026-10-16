"""Per-request correlation context, propagated into every log record."""

import logging
import re
import uuid
from contextvars import ContextVar

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")
_VALID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def new_correlation_id(incoming: str | None) -> str:
    """Accept a well-formed caller id; otherwise mint one (never trust arbitrary header text)."""
    return incoming if incoming and _VALID.match(incoming) else uuid.uuid4().hex


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True
