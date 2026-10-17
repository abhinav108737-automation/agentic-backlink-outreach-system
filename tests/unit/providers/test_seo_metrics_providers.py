"""SEO metrics providers: the three metric states are asserted separately (ADR-0015)."""

from __future__ import annotations

from pathlib import Path

import pytest
from kb_contracts.providers import (
    AuthRequired,
    InvalidResponse,
    NotConfigured,
    ProviderUnavailable,
    RateLimited,
    SEOMetricsProvider,
)
from kb_providers.seo_metrics import ManualImportSEOMetricsProvider, MockSEOMetricsProvider

REQUIRED = ["domain_authority", "spam_score"]
HEADER = "domain,metric,value,source,fetched_at\n"
ROW_OK = "a.example,domain_authority,50.5,manual_import,2026-01-05T00:00:00Z\n"


def _csv(tmp_path: Path, body: str, name: str = "m.csv") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _by_metric(metrics: list) -> dict:
    return {m.metric: m for m in metrics}


# ---------------------------------------------------------------- mock: three states
async def test_mock_state_value_present() -> None:
    got = _by_metric(
        await MockSEOMetricsProvider().get_metrics("marketinsightsjournal.com", REQUIRED)
    )
    assert got["domain_authority"].value == 72.4
    assert got["spam_score"].value == 0.1


async def test_mock_state_fetched_but_unavailable_is_none_element_present() -> None:
    got = _by_metric(
        await MockSEOMetricsProvider().get_metrics("emergingmarketspulse.io", REQUIRED)
    )
    assert "spam_score" in got and got["spam_score"].value is None
    assert got["domain_authority"].value == 61.0


async def test_mock_state_never_fetched_is_absent_not_none() -> None:
    p = MockSEOMetricsProvider()
    unknown_domain = await p.get_metrics("not-in-baseline.example", REQUIRED)
    assert unknown_domain == []
    unknown_metric = await p.get_metrics(
        "marketinsightsjournal.com", ["domain_authority", "trust_flow"]
    )
    assert [m.metric for m in unknown_metric] == ["domain_authority"]
    assert all(m.metric != "trust_flow" for m in unknown_metric)


@pytest.mark.parametrize(
    "domain", ["emergingmarketspulse.io", "newtradejournal.com", "quietdataweekly.net"]
)
async def test_defer_precondition_missing_required_metric_in_corpus(domain: str) -> None:
    got = _by_metric(await MockSEOMetricsProvider().get_metrics(domain, REQUIRED))
    missing = [m for m in REQUIRED if m not in got or got[m].value is None]
    assert missing, f"{domain} must have at least one unavailable required metric"


async def test_quietdataweekly_both_blank() -> None:
    got = _by_metric(await MockSEOMetricsProvider().get_metrics("quietdataweekly.net", REQUIRED))
    assert {m: got[m].value for m in REQUIRED} == {"domain_authority": None, "spam_score": None}


async def test_mock_lookup_is_case_and_space_insensitive_but_exact_domain() -> None:
    p = MockSEOMetricsProvider()
    assert len(await p.get_metrics("  SectorBriefing.CO.UK ", ["spam_score"])) == 1
    # no www./apex fallback: that would invent a lookup the corpus never made
    assert await p.get_metrics("energyoutlookdaily.com", REQUIRED) == []


async def test_mock_metadata_and_protocol() -> None:
    p = MockSEOMetricsProvider()
    assert isinstance(p, SEOMetricsProvider)
    assert p.is_paid is False and p.name
    health = await p.health()
    assert health.status == "ok"
    got = await p.get_metrics("sectorbriefing.co.uk", ["spam_score"])
    assert got[0].source == "fixture" and got[0].fetched_at.tzinfo is not None


# ---------------------------------------------------------------- mock: errors
async def test_mock_missing_csv_not_configured_and_health_down(tmp_path: Path) -> None:
    p = MockSEOMetricsProvider(tmp_path)
    with pytest.raises(NotConfigured):
        await p.get_metrics("a.example", REQUIRED)
    assert (await p.health()).status == "down"


async def test_mock_empty_domain_invalid_response() -> None:
    with pytest.raises(InvalidResponse):
        await MockSEOMetricsProvider().get_metrics("   ", REQUIRED)


async def test_mock_malformed_csv_invalid_response(tmp_path: Path) -> None:
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "baseline.csv").write_text(HEADER + "a.example,only,three\n")
    with pytest.raises(InvalidResponse):
        await MockSEOMetricsProvider(tmp_path).get_metrics("a.example", REQUIRED)


@pytest.mark.parametrize("err", [ProviderUnavailable, RateLimited, AuthRequired, NotConfigured])
async def test_mock_simulated_taxonomy_errors(err: type[Exception]) -> None:
    p = MockSEOMetricsProvider(simulate=err)  # type: ignore[arg-type]
    with pytest.raises(err):
        await p.get_metrics("a.example", REQUIRED)
    assert (await p.health()).status == "down"


# ---------------------------------------------------------------- manual import: three states
SAMPLE = Path(__file__).resolve().parents[3] / "fixtures" / "metrics" / "manual_import_sample.csv"


async def test_manual_state_value_present() -> None:
    got = _by_metric(
        await ManualImportSEOMetricsProvider(SAMPLE).get_metrics(
            "manualimport-one.example", REQUIRED
        )
    )
    assert got["domain_authority"].value == 66.5 and got["spam_score"].value == 0.4


async def test_manual_state_fetched_but_unavailable_is_none_element_present() -> None:
    got = _by_metric(
        await ManualImportSEOMetricsProvider(SAMPLE).get_metrics(
            "manualimport-two.example", REQUIRED
        )
    )
    assert "domain_authority" in got and got["domain_authority"].value is None
    assert got["spam_score"].value == 0.8


async def test_manual_state_never_fetched_is_absent_not_none() -> None:
    p = ManualImportSEOMetricsProvider(SAMPLE)
    assert await p.get_metrics("nobody.example", REQUIRED) == []
    # three has only spam_score: domain_authority must be absent, not None
    got = await p.get_metrics("manualimport-three.example", REQUIRED)
    assert [m.metric for m in got] == ["spam_score"]
    assert got[0].value == 1.9


async def test_manual_sample_normalises_padded_mixed_case_row() -> None:
    got = await ManualImportSEOMetricsProvider(SAMPLE).get_metrics(
        "MANUALIMPORT-THREE.EXAMPLE", ["SPAM_SCORE"]
    )
    assert got[0].domain == "manualimport-three.example" and got[0].metric == "spam_score"


async def test_manual_result_follows_request_order_and_dedupes(tmp_path: Path) -> None:
    path = _csv(
        tmp_path,
        HEADER + ROW_OK + "a.example,spam_score,0.1,manual_import,2026-01-05T00:00:00Z\n",
    )
    got = await ManualImportSEOMetricsProvider(path).get_metrics(
        "a.example", ["spam_score", "domain_authority", "spam_score"]
    )
    assert [m.metric for m in got] == ["spam_score", "domain_authority"]


# ---------------------------------------------------------------- manual import: errors
async def test_manual_missing_file_not_configured(tmp_path: Path) -> None:
    p = ManualImportSEOMetricsProvider(tmp_path / "nope.csv")
    with pytest.raises(NotConfigured):
        await p.get_metrics("a.example", REQUIRED)
    assert (await p.health()).status == "disabled"


async def test_manual_health_ok_and_not_paid() -> None:
    p = ManualImportSEOMetricsProvider(SAMPLE)
    assert (await p.health()).status == "ok"
    assert p.is_paid is False and isinstance(p, SEOMetricsProvider)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "domain,metric,value\n",
        HEADER + "a.example,domain_authority,50\n",
        HEADER + "a.example,domain_authority,50,src,2026-01-05T00:00:00Z,extra\n",
        HEADER + "a.example,domain_authority,fifty,src,2026-01-05T00:00:00Z\n",
        HEADER + "a.example,domain_authority,nan,src,2026-01-05T00:00:00Z\n",
        HEADER + "a.example,domain_authority,inf,src,2026-01-05T00:00:00Z\n",
        HEADER + ",domain_authority,5,src,2026-01-05T00:00:00Z\n",
        HEADER + "a.example,,5,src,2026-01-05T00:00:00Z\n",
        HEADER + "a.example,domain_authority,5,,2026-01-05T00:00:00Z\n",
        HEADER + "a.example,domain_authority,5,src,yesterday\n",
        HEADER + "a.example,domain_authority,5,src,2026-01-05T00:00:00\n",
        HEADER + ROW_OK + ROW_OK,
    ],
    ids=[
        "empty", "bad-header", "short-row", "long-row", "non-numeric", "nan", "inf",
        "no-domain", "no-metric", "no-source", "bad-date", "naive-date", "duplicate",
    ],
)  # fmt: skip
async def test_manual_malformed_rows_raise_invalid_response(tmp_path: Path, body: str) -> None:
    with pytest.raises(InvalidResponse):
        await ManualImportSEOMetricsProvider(_csv(tmp_path, body)).get_metrics(
            "a.example", REQUIRED
        )


async def test_manual_one_bad_row_is_not_skipped(tmp_path: Path) -> None:
    body = HEADER + ROW_OK + "b.example,spam_score,oops,src,2026-01-05T00:00:00Z\n"
    with pytest.raises(InvalidResponse):
        await ManualImportSEOMetricsProvider(_csv(tmp_path, body)).get_metrics(
            "a.example", REQUIRED
        )


async def test_manual_empty_domain_invalid_response() -> None:
    with pytest.raises(InvalidResponse):
        await ManualImportSEOMetricsProvider(SAMPLE).get_metrics("", REQUIRED)
