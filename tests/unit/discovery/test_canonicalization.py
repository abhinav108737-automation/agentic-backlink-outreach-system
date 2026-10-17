"""URL canonicalization, registered domains, stable hashing, dedupe/grouping."""

from __future__ import annotations

import hashlib

import pytest
from kb_core.discovery import (
    InvalidUrl,
    canonicalize_url,
    dedupe_candidates,
    group_by_registered_domain,
    registered_domain,
    url_hash,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM/Path", "https://example.com/Path"),  # scheme/host lower, path kept
        ("https://example.com:443/a", "https://example.com/a"),  # default https port
        ("http://example.com:80/a", "http://example.com/a"),  # default http port
        ("http://example.com:443/a", "http://example.com:443/a"),  # non-default for scheme
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://example.com/a#section", "https://example.com/a"),  # fragment
        ("https://example.com/a?utm_source=x&utm_medium=y&id=7", "https://example.com/a?id=7"),
        ("https://example.com/a?fbclid=1&gclid=2&_ga=3", "https://example.com/a"),
        ("https://example.com/a?b=2&a=1", "https://example.com/a?a=1&b=2"),  # sorted
        ("https://example.com/a/./b/../c", "https://example.com/a/c"),  # dot segments
        ("https://example.com/../../a", "https://example.com/a"),  # cannot climb above root
        ("https://example.com/a//b", "https://example.com/a/b"),
        ("https://example.com/blog/", "https://example.com/blog"),  # trailing slash stripped
        ("https://example.com", "https://example.com/"),  # root always "/"
        ("https://example.com/%7Euser/%2fx", "https://example.com/~user/%2Fx"),  # pct-encoding
        ("https://example.com./a", "https://example.com/a"),  # trailing host dot
        ("https://user:pw@example.com/a", "https://example.com/a"),  # userinfo dropped
        ("example.com/a", "https://example.com/a"),  # scheme-less -> https
        ("https://www.example.com/a", "https://www.example.com/a"),  # www kept
    ],
)
def test_canonical_form(raw: str, expected: str) -> None:
    assert canonicalize_url(raw).canonical == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "ftp://example.com/a", "https:///a", "https://x.com:abc/"]
)
def test_invalid_urls_raise(raw: str) -> None:
    with pytest.raises(InvalidUrl):
        canonicalize_url(raw)


def test_canonicalization_is_idempotent() -> None:
    once = canonicalize_url("HTTP://Ex.com:80/a/../b/?utm_x=1&z=2#f").canonical
    assert canonicalize_url(once).canonical == once


def test_url_hash_is_documented_sha256_of_canonical() -> None:
    c = canonicalize_url("https://example.com/a?id=7")
    assert c.url_hash == hashlib.sha256(b"https://example.com/a?id=7").hexdigest()
    assert c.url_hash == url_hash(c.canonical) and len(c.url_hash) == 64


def test_url_hash_pinned_value_guards_against_silent_rule_changes() -> None:
    assert canonicalize_url("https://example.com/").url_hash == (
        "0f115db062b7c0dd030b16878c99dea5c354b49dc37b38eb8846179c7783e9d7"
    )


def test_equivalent_spellings_share_a_hash_and_distinct_urls_do_not() -> None:
    a = canonicalize_url("https://EXAMPLE.com:443/x/?utm_campaign=q#top")
    b = canonicalize_url("https://example.com/x")
    c = canonicalize_url("https://example.com/y")
    assert a.url_hash == b.url_hash and a.url_hash != c.url_hash


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("example.com", "example.com"),
        ("blog.datapulseanalytics.io", "datapulseanalytics.io"),
        ("www.energyoutlookdaily.com", "energyoutlookdaily.com"),
        ("apacindustrynotes.com.au", "apacindustrynotes.com.au"),
        ("news.apacindustrynotes.com.au", "apacindustrynotes.com.au"),
        ("localcafereviews.co.uk", "localcafereviews.co.uk"),
        ("a.b.sectorbriefing.co.uk", "sectorbriefing.co.uk"),
        ("EXAMPLE.COM.", "example.com"),
        ("localhost", "localhost"),
        ("10.0.0.1", "10.0.0.1"),
    ],
)
def test_registered_domain(host: str, expected: str) -> None:
    assert registered_domain(host) == expected


def test_registered_domain_known_wrong_cases_are_pinned() -> None:
    # Documented limitations: unlisted multi-label and private suffixes are mishandled.
    assert registered_domain("shop.example.com.ng") == "com.ng"
    assert registered_domain("alice.github.io") == "github.io"
    assert registered_domain("bob.github.io") == "github.io"


def test_registered_domain_empty_host_raises() -> None:
    with pytest.raises(InvalidUrl):
        registered_domain(" ")


def test_dedupe_by_canonical_form_keeps_first_in_order() -> None:
    urls = [
        "https://Example.com/a/?utm_source=x",
        "https://other.org/",
        "https://example.com:443/a#frag",
        "https://example.com/b",
    ]
    out = dedupe_candidates(urls)
    assert [c.canonical for c in out] == [
        "https://example.com/a",
        "https://other.org/",
        "https://example.com/b",
    ]
    assert out[0].original == urls[0]


def test_dedupe_does_not_swallow_invalid_input() -> None:
    with pytest.raises(InvalidUrl):
        dedupe_candidates(["https://a.com/", "mailto:x@y.com"])


def test_group_by_registered_domain() -> None:
    groups = group_by_registered_domain(
        [
            "https://www.sectorbriefing.co.uk/b",
            "https://sectorbriefing.co.uk/a",
            "https://sectorbriefing.co.uk/a/",
            "https://blog.datapulseanalytics.io/x",
            "https://shop.apacindustrynotes.com.au/",
        ]
    )
    assert list(groups) == [
        "apacindustrynotes.com.au",
        "datapulseanalytics.io",
        "sectorbriefing.co.uk",
    ]
    assert [c.canonical for c in groups["sectorbriefing.co.uk"]] == [
        "https://sectorbriefing.co.uk/a",
        "https://www.sectorbriefing.co.uk/b",
    ]
