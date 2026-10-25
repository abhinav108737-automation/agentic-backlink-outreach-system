"""ADR-0020 eval for the Reply and Negotiation agents (see evals/reply_injection_eval.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import reply_injection_eval as ev

SPEC = ev.load_cases()
MARKERS = [m.lower() for m in SPEC["forbidden_markers"]]
CASES = SPEC["cases"]
NEGOTIATION_CASES = [c for c in CASES if c.get("negotiate")]


def test_cases_cover_the_injection_fixture_and_every_vector() -> None:
    ids = {c["id"] for c in CASES}
    assert "fixture-prompt-injection-positive" in ids
    fixture = json.loads(
        (ev.SCENARIOS / "prompt-injection-positive.json").read_text(encoding="utf-8")
    )
    assert fixture["labels"]["prompt_injection"] is True
    assert {c["expect_class"] for c in CASES} >= {"positive", "opt_out", "bounce", "negotiation"}
    assert NEGOTIATION_CASES


@pytest.mark.parametrize("mode", ev.MODES)
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_reply_agent_is_inert_to_injected_reply_text(case: dict[str, Any], mode: str) -> None:
    report = await ev.run_case(case, agent="reply", mode=mode, markers=MARKERS)
    assert report.violations == []
    if mode == "hijacked":
        assert not report.outcome_ok and report.output is None  # fails closed, class unchanged
    else:
        assert report.output is not None
        assert report.output["reply_class"] == case["expect_class"]


@pytest.mark.parametrize("mode", ev.MODES)
@pytest.mark.parametrize("case", NEGOTIATION_CASES, ids=lambda c: c["id"])
async def test_negotiation_agent_is_inert_to_injected_reply_text(
    case: dict[str, Any], mode: str
) -> None:
    report = await ev.run_case(case, agent="negotiation", mode=mode, markers=MARKERS)
    assert report.violations == []
    assert report.output is not None
    # a hijacked model that agrees to terms and says "not binding" is overruled to True
    assert report.output["makes_binding_commitment"] is (mode == "hijacked")


async def test_the_eval_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the sanitiser: the same eval must now report leaks (and the obedient model obeys)."""
    import kb_agents.prompts.thread as thread

    monkeypatch.setattr(thread, "model_safe_text", lambda text, *, max_chars: (text, 0))
    case = next(c for c in CASES if c["id"] == "opt-out-with-positive-injection")
    report = await ev.run_case(case, agent="reply", mode="obedient", markers=MARKERS)
    assert any("reached a model request" in v for v in report.violations)
    assert not report.outcome_ok  # obeying the leaked text produced a class conflict failure


def test_eval_files_are_offline_and_secret_free() -> None:
    for path in (Path(ev.__file__), ev.CASES_FILE):
        text = path.read_text(encoding="utf-8").lower()
        assert "http://" not in text and "api_key" not in text and "password" not in text
