"""Cursor pagination (C8). Offsets are never exposed: they skip or repeat rows under writes."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final
from uuid import UUID

_CURSOR_V: Final = 1


class InvalidCursor(ValueError):
    """The caller sent a cursor this build cannot decode. Never silently fall back to page 1."""


class CursorPage[T]:
    """Concrete `kb_core.db.protocols.Page`.

    Consumers depend only on `items` and `next_cursor`; the cursor body is an implementation
    detail and is deliberately opaque (base64url of a versioned JSON keyset) so that changing
    the sort key later cannot break callers that stored a cursor.
    """

    __slots__ = ("items", "next_cursor")

    def __init__(self, items: Sequence[T], next_cursor: str | None = None) -> None:
        self.items: Sequence[T] = tuple(items)
        self.next_cursor: str | None = next_cursor

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CursorPage(items={len(self.items)}, next_cursor={self.next_cursor!r})"


def encode_cursor(created_at: datetime, row_id: UUID) -> str:
    """Stable keyset cursor over the `(created_at, id)` ordering every listing uses."""
    body = json.dumps(
        {"v": _CURSOR_V, "t": created_at.isoformat(), "i": str(row_id)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    pad = "=" * (-len(cursor) % 4)
    try:
        raw: Any = json.loads(base64.urlsafe_b64decode(cursor + pad))
        if not isinstance(raw, dict) or raw.get("v") != _CURSOR_V:
            raise InvalidCursor(f"unsupported cursor version: {cursor!r}")
        return datetime.fromisoformat(str(raw["t"])), UUID(str(raw["i"]))
    except InvalidCursor:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise InvalidCursor(f"undecodable cursor: {cursor!r}") from exc
