"""CCR-002: NaN sentinels are forbidden in contract DTOs and API payloads.

A NaN sentinel looks safe because it fails every threshold comparison, but it cannot survive
serialization: Pydantic emits `null` for it, that `null` then fails validation on the way back,
and strict JSON refuses NaN outright. An "unavailable" value must therefore be `None`, which is
representable, round-trippable and explicit. These tests exist so the sentinel cannot come back.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from kb_contracts.domain import DomainMetric
from kb_contracts.providers import SEOMetric
from pydantic import ValidationError

_NOW = datetime.now(UTC)


def _domain_metric(value: float | None) -> DomainMetric:
    return DomainMetric(
        id=uuid4(),
        created_at=_NOW,
        updated_at=_NOW,
        domain_id=uuid4(),
        provider="fixture",
        metric="spam_score",
        value=value,
        fetched_at=_NOW,
        source="fixture",
    )


@pytest.mark.req("FR-05")
def test_unavailable_metric_is_none_and_round_trips():
    """The case the NaN sentinel used to break: unavailable must survive a JSON round-trip."""
    m = _domain_metric(None)
    assert m.value is None
    restored = DomainMetric.model_validate_json(m.model_dump_json())
    assert restored.value is None
    assert restored == m


@pytest.mark.req("FR-05")
def test_metric_with_a_value_round_trips():
    m = _domain_metric(0.4)
    assert DomainMetric.model_validate_json(m.model_dump_json()).value == 0.4


@pytest.mark.req("FR-05")
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_contract_dtos_reject_non_finite_values(bad: float):
    """No caller may smuggle a NaN/inf sentinel back in through either metric DTO."""
    with pytest.raises(ValidationError):
        _domain_metric(bad)
    with pytest.raises(ValidationError):
        SEOMetric(domain="x.example", metric="spam_score", value=bad, source="s", fetched_at=_NOW)


@pytest.mark.req("FR-05")
def test_payloads_are_strict_json_serializable():
    """`allow_nan=False` is what an HTTP client and a strict parser effectively enforce."""
    for value in (None, 0.0, 45.5):
        payload = _domain_metric(value).model_dump(mode="json")
        json.dumps(payload, allow_nan=False)  # raises if any NaN/inf leaked in


@pytest.mark.req("FR-05")
def test_the_two_metric_dtos_agree_on_nullability():
    """`SEOMetric` (provider side) and `DomainMetric` (persisted side) must not disagree —
    that disagreement was the original defect CCR-002 fixed."""
    for model in (DomainMetric, SEOMetric):
        assert type(None) in model.model_fields["value"].annotation.__args__, model.__name__


@pytest.mark.req("FR-05")
def test_nan_is_not_silently_accepted_by_math_guard():
    """Guards against a future 'helpful' coercion of NaN to None inside the DTO."""
    with pytest.raises(ValidationError):
        _domain_metric(float("nan"))
    assert not math.isnan(_domain_metric(0.0).value or 0.0)
