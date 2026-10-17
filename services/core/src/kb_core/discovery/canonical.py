"""URL canonicalization, registered-domain extraction and stable URL hashing.

Canonicalization rules (``CANONICALIZATION_VERSION`` = 1). Changing any rule changes
``url_hash`` for existing rows and therefore REQUIRES bumping the version and a backfill
migration, because ``prospect_urls`` is ``UNIQUE(prospect_id, url_hash)``.

1. A scheme-less input is assumed to be ``https``. Only ``http``/``https`` are accepted; any
   other scheme, an empty host or an unparseable port raises :class:`InvalidUrl`.
2. Scheme and host are lowercased. A trailing dot on the host is removed. Userinfo is dropped.
3. A port equal to the scheme default (80/http, 443/https) is removed; other ports are kept.
4. The fragment is dropped.
5. The path has dot segments resolved (RFC 3986 section 5.2.4), repeated slashes collapsed and
   percent-escapes normalised (unreserved characters decoded, others upper-cased hex).
6. Trailing-slash policy: a non-root path has its trailing slash REMOVED
   (``/blog/`` == ``/blog``); the root path is always ``/``. This is a deliberate policy, not
   a claim that servers treat the two identically.
7. Known tracking parameters are removed (``utm_*``, ``fbclid``, ``gclid``, ...). Remaining
   parameters keep their values and are sorted by (name, value); an empty query is omitted.
8. ``www.`` is NOT stripped from URLs: ``www.a.com`` and ``a.com`` are different canonical URLs
   that share one registered domain.

``url_hash`` is the lowercase hex SHA-256 of the UTF-8 canonical URL (64 characters).

Registered domain: see :func:`registered_domain` and its documented limitation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

CANONICALIZATION_VERSION = 1

_DEFAULT_PORTS = {"http": 80, "https": 443}

_TRACKING_EXACT = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "msclkid",
        "yclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "mkt_tok",
        "vero_id",
    }
)
_TRACKING_PREFIXES = ("utm_",)

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")

# Multi-label public suffixes this module knows. This is a small hand-maintained subset of the
# Public Suffix List, NOT the list itself.
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "sch.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
        "co.nz", "org.nz", "net.nz", "ac.nz", "govt.nz",
        "co.in", "net.in", "org.in", "ac.in", "gov.in", "firm.in",
        "co.jp", "ne.jp", "or.jp", "ac.jp",
        "com.br", "com.cn", "com.sg", "com.hk", "com.mx", "com.tr", "com.ar",
        "com.my", "com.ph", "com.pk", "co.za", "co.kr", "co.il",
    }
)  # fmt: skip

_OPAQUE_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:(?!\d)")  # mailto:, javascript:, tel:

_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class InvalidUrl(ValueError):
    """The input cannot be canonicalized to an http(s) URL with a host."""


@dataclass(frozen=True)
class CanonicalUrl:
    original: str
    canonical: str
    url_hash: str
    host: str
    registered_domain: str


def registered_domain(host: str) -> str:
    """Return the registrable domain of ``host`` (deterministic, no external library).

    Rules: lowercase, strip a trailing dot; IP literals and single-label hosts are returned
    unchanged; if the last two labels are in a built-in list of common multi-label public
    suffixes (``co.uk``, ``com.au``, ...) the last THREE labels are returned; otherwise the last
    TWO labels.

    Limitations (no Public Suffix List is bundled; this is intentional and honest):
      * Multi-label suffixes NOT in the built-in list are mishandled: ``shop.example.com.ng``
        yields ``com.ng`` (too short), collapsing every ``*.com.ng`` site into one domain.
      * Private-section suffixes (``github.io``, ``blogspot.com``, ``herokuapp.com``) are not
        recognised, so ``alice.github.io`` and ``bob.github.io`` both yield ``github.io``.
      * Wildcard/exception PSL rules (``*.ck``, ``!www.ck``) are not modelled.
      * IDN hosts are compared as given (no punycode normalisation); IPv6 literals are returned
        unchanged only when bracket-free input is passed.
    Callers needing exactness for a specific TLD should extend ``_MULTI_LABEL_SUFFIXES``.
    """
    h = host.strip().lower().rstrip(".")
    if not h:
        raise InvalidUrl("empty host")
    if _IPV4.match(h) or ":" in h:
        return h
    labels = h.split(".")
    if len(labels) <= 2:
        return h
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _normalise_percent(path: str) -> str:
    def repl(m: re.Match[str]) -> str:
        ch = chr(int(m.group(1), 16))
        return ch if ch in _UNRESERVED else f"%{m.group(1).upper()}"

    return _PCT.sub(repl, path)


def _resolve_path(path: str) -> str:
    out: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out:
                out.pop()
            continue
        out.append(seg)
    return "/" + "/".join(out)  # no trailing slash for non-root; "/" for root


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_EXACT or lowered.startswith(_TRACKING_PREFIXES)


def canonicalize_url(url: str) -> CanonicalUrl:
    """Canonicalize ``url`` per the module rules and attach hash + registered domain."""
    raw = url.strip()
    if not raw:
        raise InvalidUrl("empty URL")
    if "//" not in raw and _OPAQUE_SCHEME.match(raw):
        raise InvalidUrl(f"unsupported scheme in {url!r}")
    parts = urlsplit(raw if "//" in raw else f"https://{raw}")
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise InvalidUrl(f"unsupported scheme {parts.scheme!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidUrl(f"no host in {url!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidUrl(f"bad port in {url!r}") from exc
    netloc = host if port is None or port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"
    path = _resolve_path(_normalise_percent(parts.path))
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    ]
    query = urlencode(sorted(pairs))
    canonical = f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")
    return CanonicalUrl(
        original=url,
        canonical=canonical,
        url_hash=url_hash(canonical),
        host=host,
        registered_domain=registered_domain(host),
    )


def url_hash(canonical_url: str) -> str:
    """Stable identity hash of an ALREADY canonical URL (lowercase hex SHA-256)."""
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def dedupe_candidates(urls: Iterable[str]) -> list[CanonicalUrl]:
    """Collapse ``urls`` by canonical form, keeping the first occurrence in input order.

    Inputs that cannot be canonicalized raise :class:`InvalidUrl` (never silently dropped).
    """
    seen: set[str] = set()
    out: list[CanonicalUrl] = []
    for url in urls:
        item = canonicalize_url(url)
        if item.url_hash in seen:
            continue
        seen.add(item.url_hash)
        out.append(item)
    return out


def group_by_registered_domain(
    urls: Iterable[str],
) -> dict[str, list[CanonicalUrl]]:
    """Dedupe then group by registered domain; keys and members are in sorted order."""
    groups: dict[str, list[CanonicalUrl]] = {}
    for item in dedupe_candidates(urls):
        groups.setdefault(item.registered_domain, []).append(item)
    return {
        domain: sorted(members, key=lambda c: c.canonical)
        for domain, members in sorted(groups.items())
    }
