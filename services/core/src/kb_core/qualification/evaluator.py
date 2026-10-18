"""Hard eligibility (pure, deterministic, no LLM): DA > min_da, Spam < max_spam, DEFER on missing.

The three metric states are kept distinct end to end (ADR-0015):

  1. fetched with a number    -> `latest[m]` is a finite float
  2. fetched but unavailable  -> `latest[m]` is None
  3. never fetched            -> `m` is absent from `latest`

States 2 and 3 are both "missing" for a *required* metric and both yield DEFERRED (or REJECTED
only if the policy says `on_missing_metric == "reject"`). Neither is ever read as 0, as a pass,
or as a failed threshold. `DomainMetric.value` is deliberately not consulted: only the
`DomainMetricRepository.latest` mapping decides availability.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from kb_contracts.domain import PolicyBody
from kb_contracts.enums import QualificationOutcome

METRIC_DA = "domain_authority"
METRIC_SPAM = "spam_score"

# Stable machine-readable reason codes recorded on every decision.
R_DA_FAILED = "DA_NOT_ABOVE_MIN"
R_SPAM_FAILED = "SPAM_NOT_BELOW_MAX"
R_DEFERRED = "DEFERRED_MISSING_METRICS"
R_REJECT_ON_MISSING = "REJECTED_MISSING_METRICS_BY_POLICY"
R_ELIGIBLE = "ALL_REQUIRED_METRICS_MET"
R_UNAVAILABLE = "METRIC_UNAVAILABLE"  # state 2, suffixed ":<metric>"
R_NEVER_FETCHED = "METRIC_NEVER_FETCHED"  # state 3, suffixed ":<metric>"


class MetricState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NEVER_FETCHED = "never_fetched"


def classify_metric(latest: Mapping[str, float | None], metric: str) -> MetricState:
    if metric not in latest:
        return MetricState.NEVER_FETCHED
    value = latest[metric]
    if value is None or not math.isfinite(value):  # a non-finite reading is not a value
        return MetricState.UNAVAILABLE
    return MetricState.AVAILABLE


def metrics_to_fetch(body: PolicyBody) -> tuple[str, ...]:
    """Required metrics plus DA/spam, which are thresholded whenever a value is known."""
    seen: dict[str, None] = dict.fromkeys(body.required_metrics)
    seen.setdefault(METRIC_DA, None)
    seen.setdefault(METRIC_SPAM, None)
    return tuple(seen)


@dataclass(frozen=True)
class Evaluation:
    outcome: QualificationOutcome
    reason_codes: tuple[str, ...]
    # Only metrics that were fetched appear here (value or None); never-fetched ones are absent.
    metric_snapshot: dict[str, float | None]
    missing_metrics: tuple[str, ...]
    metric_states: dict[str, MetricState]
    threshold_failures: tuple[str, ...]


def evaluate(body: PolicyBody, latest: Mapping[str, float | None]) -> Evaluation:
    states = {m: classify_metric(latest, m) for m in metrics_to_fetch(body)}
    snapshot: dict[str, float | None] = {
        m: (None if states[m] is MetricState.UNAVAILABLE else latest[m])
        for m in states
        if states[m] is not MetricState.NEVER_FETCHED
    }

    missing = tuple(m for m in body.required_metrics if states[m] is not MetricState.AVAILABLE)
    reasons: list[str] = []
    for m in missing:
        prefix = R_NEVER_FETCHED if states[m] is MetricState.NEVER_FETCHED else R_UNAVAILABLE
        reasons.append(f"{prefix}:{m}")

    failures: list[str] = []
    da, spam = latest.get(METRIC_DA), latest.get(METRIC_SPAM)
    if states[METRIC_DA] is MetricState.AVAILABLE and da is not None and da <= body.min_da:
        failures.append(R_DA_FAILED)  # strict: DA must be > min_da
    if states[METRIC_SPAM] is MetricState.AVAILABLE and spam is not None and spam >= body.max_spam:
        failures.append(R_SPAM_FAILED)  # strict: spam must be < max_spam
    reasons.extend(failures)

    if missing:
        if body.on_missing_metric == "reject":
            outcome, tag = QualificationOutcome.REJECTED, R_REJECT_ON_MISSING
        else:
            outcome, tag = QualificationOutcome.DEFERRED, R_DEFERRED
    elif failures:
        outcome, tag = QualificationOutcome.REJECTED, ""
    else:
        outcome, tag = QualificationOutcome.ELIGIBLE, R_ELIGIBLE
    if tag:
        reasons.append(tag)

    return Evaluation(
        outcome=outcome,
        reason_codes=tuple(reasons),
        metric_snapshot=snapshot,
        missing_metrics=missing,
        metric_states=states,
        threshold_failures=tuple(failures),
    )


def explain_decision(outcome: QualificationOutcome, reason_codes: Sequence[str]) -> str:
    """Human-readable sentence for the UI, built only from the recorded outcome and reason codes
    so it always matches what was decided, not today's readings."""
    if outcome is QualificationOutcome.ELIGIBLE:
        return "Eligible: every required metric is available and meets the policy thresholds."
    parts: list[str] = []
    for code in reason_codes:
        head, _, metric = code.partition(":")
        if head == R_NEVER_FETCHED:
            parts.append(f"{metric} was never fetched")
        elif head == R_UNAVAILABLE:
            parts.append(f"{metric} was fetched but is unavailable")
        elif head == R_DA_FAILED:
            parts.append("domain authority is not above the minimum")
        elif head == R_SPAM_FAILED:
            parts.append("spam score is not below the maximum")
    lead = "Deferred (not passed)" if outcome is QualificationOutcome.DEFERRED else "Rejected"
    return f"{lead}: " + ("; ".join(parts) if parts else ", ".join(reason_codes)) + "."


def explain(evaluation: Evaluation) -> str:
    return explain_decision(evaluation.outcome, evaluation.reason_codes)
