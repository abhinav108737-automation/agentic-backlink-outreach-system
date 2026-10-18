"""Explainable ranking (FR-26): a weighted mean of normalised signals, fully decomposed.

Signals are in [0, 1]. Weights come from the effective `PolicyBody.ranking_weights`. A signal
that is not available is *omitted and named in the explanation*; it is never treated as 0, and
the remaining weights are renormalised so a missing input neither helps nor silently hurts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from kb_core.errors import InvalidInput

# Components named by FR-26. Policy weights decide which are used.
KNOWN_SIGNALS = (
    "relevance",
    "authority",
    "editorial_fit",
    "traffic",
    "contactability",
    "publication_likelihood",
    "history",
    "priority",
    "risk",
)


@dataclass(frozen=True)
class RankingComputation:
    score: float
    components: dict[str, float]
    explanation: str


def compute_ranking(
    weights: Mapping[str, float], signals: Mapping[str, float]
) -> RankingComputation:
    for name, value in signals.items():
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise InvalidInput(f"signal {name!r} must be a finite number within [0, 1]")
    for name, weight in weights.items():
        if not math.isfinite(weight) or weight < 0:
            raise InvalidInput(f"weight {name!r} must be finite and >= 0")

    equal = not any(w > 0 for w in weights.values())
    effective = {k: 1.0 for k in signals} if equal else {k: w for k, w in weights.items() if w > 0}
    used = {k: effective[k] for k in effective if k in signals}
    total = sum(used.values())
    if total <= 0:
        raise InvalidInput("no ranking signal overlaps the policy's weighted components")

    components = {k: round(w / total * signals[k], 6) for k, w in used.items()}
    score = round(sum(components.values()), 6)
    unavailable = sorted(set(effective) - set(signals))
    ignored = sorted(set(signals) - set(effective))

    terms = " + ".join(
        f"{k} {components[k]:.3f} (weight {w / total:.2f} x signal {signals[k]:.2f})"
        for k, w in sorted(used.items())
    )
    parts = [f"score {score:.3f} = {terms}"]
    if equal:
        parts.append("policy defines no weights, so signals are weighted equally")
    if unavailable:
        parts.append("not available (excluded, weights renormalised): " + ", ".join(unavailable))
    if ignored:
        parts.append("signals with no policy weight (ignored): " + ", ".join(ignored))
    return RankingComputation(score, components, "; ".join(parts) + ".")
