"""Suppression value normalisation. Pure functions; the repository re-normalises in SQL too."""

from __future__ import annotations

from urllib.parse import urlsplit

from kb_contracts.enums import SuppressionScope

from kb_core.discovery.canonical import InvalidUrl, registered_domain
from kb_core.errors import InvalidInput


def normalize_email(raw: str) -> str:
    value = raw.strip().lower()
    local, sep, host = value.rpartition("@")
    if not sep or not local or not host or " " in value or "." not in host:
        raise InvalidInput(f"not a valid email address: {raw!r}")
    return value


def normalize_domain(raw: str) -> str:
    """Registered domain of a host, URL or email domain: `https://www.Blog.Example.com/x` and
    `example.com` converge on `example.com`, so suppressing one covers the other."""
    text = raw.strip().lower()
    if "://" in text:
        text = urlsplit(text).hostname or ""
    text = text.split("/")[0].split("@")[-1].rstrip(".")
    try:
        return registered_domain(text)
    except InvalidUrl as exc:
        raise InvalidInput(f"not a valid domain: {raw!r}") from exc


def normalize_value(scope: SuppressionScope, raw: str) -> str:
    if scope is SuppressionScope.EMAIL:
        return normalize_email(raw)
    if scope is SuppressionScope.DOMAIN:
        return normalize_domain(raw)
    value = raw.strip().lower()
    if not value:
        raise InvalidInput("contact suppression value must not be empty")
    return value
