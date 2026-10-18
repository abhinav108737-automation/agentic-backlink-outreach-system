"""ADR-0020 eval over the three prompt_injection fixture sites (see evals/)."""

import injection_eval
import pytest

CASES = injection_eval.load_cases()
MARKERS = [m.lower() for m in CASES["forbidden_markers"]]


def test_cases_cover_every_labelled_fixture_site() -> None:
    import json
    from pathlib import Path

    sites = Path(__file__).resolve().parents[3] / "fixtures" / "sites"
    labelled = {
        p.parent.name
        for p in sites.glob("*/site.json")
        if json.loads(p.read_text())["labels"].get("prompt_injection")
    }
    assert labelled == {c["domain"] for c in CASES["cases"]}
    assert len(labelled) == 3


@pytest.mark.parametrize("obedient", [False, True], ids=["compliant-model", "obedient-model"])
@pytest.mark.parametrize("domain", [c["domain"] for c in CASES["cases"]])
async def test_injection_does_not_change_calls_scope_or_output(domain: str, obedient: bool) -> None:
    report = await injection_eval.run_case(domain, obedient=obedient, markers=MARKERS)
    assert report.violations == []
    assert set(report.outputs) == {"research", "contact", "qualification"}


async def test_obedient_and_compliant_models_yield_same_deterministic_facts() -> None:
    domain = CASES["cases"][2]["domain"]  # contact-page-inject.io
    a = await injection_eval.run_case(domain, obedient=False, markers=MARKERS)
    b = await injection_eval.run_case(domain, obedient=True, markers=MARKERS)
    values = lambda r: sorted(  # noqa: E731
        (c["channel_type"], c["value"]) for c in r.outputs["contact"]["candidates"]
    )
    assert values(a) == values(b)
    assert a.outputs["research"]["topics"] == b.outputs["research"]["topics"]
    assert not any("attacker" in v for _, v in values(b))
