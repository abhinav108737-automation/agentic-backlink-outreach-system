"""I1: the agent-driven vertical slice, end to end against real wiring.

Nothing here is faked except the two things that must be: the model (deterministic, offline) and
the external providers (fixture-backed mocks). The ToolGateway, its capabilities, the
repositories, the PolicyEngine, the transitions, the audit trail and the HTTP API are all real,
and the database is a real migrated PostgreSQL clone.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from kb_contracts.enums import AgentName, ProspectStatus, QualificationOutcome
from kb_contracts.tools import FORBIDDEN_CAPABILITIES, TOOL_ALLOWLIST
from kb_core.audit.service import OPERATOR
from kb_core.db import tables as t
from kb_runtime import persist_discovery, run_prospect_slice

pytestmark = pytest.mark.db

REQUIRED = ("domain_authority", "spam_score")

# The corpus' three "fetched but unavailable" domains (state 2). Kept in step with
# fixtures/metrics/baseline.csv, where each has a blank value for a required metric.
DEFER_DOMAINS = (
    "emergingmarketspulse.io",
    "newtradejournal.com",
    "quietdataweekly.net",
)


async def _discover(stack: Any, campaign: uuid.UUID) -> Any:
    return await persist_discovery(stack=stack, campaign_id=campaign)


async def _prospects(factory: Any, campaign: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    async with factory() as uow:
        page = await uow.prospects.list(campaign_id=campaign, limit=50)
        out = []
        for p in page.items:
            dom = await uow.domains.get(p.domain_id)
            out.append((p.id, dom.registered_domain if dom else ""))
    return out


async def _make_prospect(factory: Any, campaign: uuid.UUID, domain: str) -> uuid.UUID:
    async with factory() as uow:
        d = await uow.domains.get_or_create(domain)
        p = await uow.prospects.create(campaign_id=campaign, domain_id=d.id, discovered_via="test")
    return p.id


async def _slice(stack, factory, services, campaign, prospect_id):  # noqa: ANN001
    return await run_prospect_slice(
        stack=stack,
        uow_factory=factory,
        qualification=services.qualification,
        ranking=services.ranking,
        transitions=services.transitions,
        campaign_id=campaign,
        prospect_id=prospect_id,
        actor=OPERATOR,
        correlation_id="i1-test",
    )


# ------------------------------------------------------------------ the vertical slice


async def test_agent_driven_slice_reaches_contact_ready(stack, seeded, services, campaign):
    """Discovery -> Qualification -> Research -> Contact, driven by agents through the real
    gateway, ending in a persisted CONTACT_READY prospect with evidence and a contact channel."""
    discovery = await _discover(stack, campaign)
    assert discovery.ok, discovery.record.failure
    assert [a.tool for a in stack.tool_calls] == [
        "read_campaign_state",
        "search",
        "record_candidates",
    ]

    eligible = None
    for prospect_id, host in await _prospects(seeded, campaign):
        out = await _slice(stack, seeded, services, campaign, prospect_id)
        if out.outcome is QualificationOutcome.ELIGIBLE:
            eligible = (prospect_id, host, out)
    assert eligible is not None, "the corpus must yield at least one eligible prospect"
    prospect_id, host, out = eligible

    assert out.status is ProspectStatus.CONTACT_READY
    assert out.researched is True
    assert out.contacts > 0
    assert out.rank_score is not None

    async with seeded() as uow:
        prospect = await uow.prospects.get(prospect_id)
        decision = await uow.qualifications.latest(prospect_id)
        ranking = await uow.rankings.latest(prospect_id)
        dossier = await uow.dossiers.latest(prospect_id)
        evidence = await uow.evidence.list_for_prospect(prospect_id)
        channels = await uow.contacts.list_channels(prospect.domain_id)
    assert prospect.status is ProspectStatus.CONTACT_READY
    assert decision is not None and decision.outcome is QualificationOutcome.ELIGIBLE
    assert ranking is not None and dossier is not None
    assert evidence, "research must persist evidence"
    assert channels and all(
        c.value_normalized == c.value_normalized.strip().lower() for c in channels
    )
    # Every contact channel belongs to the prospect's own domain: no cross-domain leakage.
    assert all(host in c.value_normalized or c.channel_type != "email" for c in channels)


# ------------------------------------------------------------------------ DEFER cases


@pytest.mark.parametrize("domain", DEFER_DOMAINS)
async def test_fetched_but_unavailable_metric_defers(
    stack, seeded, services, campaign, domain: str
):
    """State 2: the provider returned the metric with no value. Three corpus domains."""
    prospect_id = await _make_prospect(seeded, campaign, domain)
    out = await _slice(stack, seeded, services, campaign, prospect_id)
    assert out.outcome is QualificationOutcome.DEFERRED, domain
    assert out.missing_metrics, domain
    assert out.status is ProspectStatus.DEFERRED
    assert out.researched is False and out.contacts == 0

    async with seeded() as uow:
        decision = await uow.qualifications.latest(prospect_id)
    assert decision is not None
    # The snapshot distinguishes state 2 from state 3: the key is present and null.
    for metric in decision.missing_metrics:
        assert decision.metric_snapshot.get(metric, "absent") is None
    assert any(r.startswith("METRIC_UNAVAILABLE:") for r in decision.reason_codes)


async def test_never_fetched_metric_defers(stack, seeded, services, campaign):
    """State 3: no reading exists at all. The fourth missing-metric case."""
    prospect_id = await _make_prospect(seeded, campaign, "unlisted-no-metrics.example")
    out = await _slice(stack, seeded, services, campaign, prospect_id)
    assert out.outcome is QualificationOutcome.DEFERRED
    assert set(out.missing_metrics) == set(REQUIRED)
    assert out.status is ProspectStatus.DEFERRED

    async with seeded() as uow:
        decision = await uow.qualifications.latest(prospect_id)
    assert decision is not None
    # Absent from the snapshot entirely — not null, and certainly not zero.
    for metric in REQUIRED:
        assert metric not in decision.metric_snapshot
    assert any(r.startswith("METRIC_NEVER_FETCHED:") for r in decision.reason_codes)


async def test_three_metric_states_survive_the_gateway(stack, seeded, campaign, agent_run):
    """The gateway's read_domain_metrics must preserve float / None / absent."""
    box = stack.gateway.bind(AgentName.QUALIFICATION, agent_run, correlation_id="i1")
    r = await box.call(
        "read_domain_metrics",
        {"domain": "emergingmarketspulse.io", "metrics": (*REQUIRED, "trust_flow")},
    )
    assert r.ok, r.error_code
    metrics = r.data["metrics"]
    assert isinstance(metrics["domain_authority"], float)  # fetched with a value
    assert metrics["spam_score"] is None  # fetched, unavailable
    assert "trust_flow" not in metrics  # never fetched
    assert json.dumps(r.data, allow_nan=False, default=str)  # no NaN reaches a payload


# ----------------------------------------------------------- duplicates and provenance


async def test_discovery_is_idempotent_and_creates_no_duplicates(stack, seeded, campaign):
    await _discover(stack, campaign)
    async with seeded() as uow:
        first = await uow.prospects.list(campaign_id=campaign, limit=50)
        first_urls = await uow.session.execute(
            sa.select(sa.func.count()).select_from(t.prospect_urls)
        )
        urls_before = first_urls.scalar_one()

    await _discover(stack, campaign)  # same campaign, same fixtures
    async with seeded() as uow:
        second = await uow.prospects.list(campaign_id=campaign, limit=50)
        again = await uow.session.execute(sa.select(sa.func.count()).select_from(t.prospect_urls))
        urls_after = again.scalar_one()

    assert len(second.items) == len(first.items), "re-discovery must not add prospects"
    assert urls_after == urls_before, "re-discovery must not add prospect_urls"


async def test_provenance_is_recorded_for_every_discovered_url(stack, seeded, campaign):
    discovery = await _discover(stack, campaign)
    assert discovery.ok
    async with seeded() as uow:
        rows = (await uow.session.execute(sa.select(t.prospect_urls))).mappings().all()
    assert rows
    for row in rows:
        prov = row["provenance"]
        assert prov["source"], "a discovered url must say where it came from"
        assert prov["agent_run_id"] and prov["agent_run_id"] != "None"
        assert row["canonical_url"] and row["url_hash"]


# ------------------------------------------------------------------- tool-scope safety


async def test_forbidden_and_out_of_scope_tools_are_refused_and_change_nothing(
    stack, seeded, campaign, agent_run
):
    """Through the REAL gateway with real capabilities wired: a forbidden capability and an
    out-of-scope tool must be refused, persist nothing, and still be audited."""
    async with seeded() as uow:
        before = (
            await uow.session.execute(sa.select(sa.func.count()).select_from(t.prospects))
        ).scalar_one()

    uuid.uuid4()
    async with seeded() as uow:
        agent_run_id = await uow.agent_runs.start(
            agent="discovery",
            task_type="scope-probe",
            campaign_id=campaign,
            input_refs={},
            model=None,
            provider=None,
            correlation_id="i1-scope",
        )
    box = stack.gateway.bind(AgentName.DISCOVERY, agent_run_id, correlation_id="i1-scope")

    for tool in sorted(FORBIDDEN_CAPABILITIES):
        r = await box.call(tool, {})
        assert not r.ok and r.policy_blocked, tool

    # record_dossier is a real wired capability, but not on DISCOVERY's allowlist.
    assert "record_dossier" not in TOOL_ALLOWLIST[AgentName.DISCOVERY]
    denied = await box.call(
        "record_dossier",
        {
            "dossier": {
                "prospect_id": str(uuid.uuid4()),
                "summary": "x",
                "topics": (),
                "evidence_ids": (),
                "unsupported_claims": (),
            }
        },
    )
    assert not denied.ok and denied.policy_blocked

    async with seeded() as uow:
        after = (
            await uow.session.execute(sa.select(sa.func.count()).select_from(t.prospects))
        ).scalar_one()
        calls = (
            (
                await uow.session.execute(
                    sa.select(t.tool_calls).where(t.tool_calls.c.agent_run_id == agent_run_id)
                )
            )
            .mappings()
            .all()
        )
    assert after == before, "a refused tool must not write"
    assert len(calls) == len(FORBIDDEN_CAPABILITIES) + 1, "every refusal must be audited"
    assert all(c["status"] != "ok" for c in calls)
    assert all(c["tool_name"] != "send_email_direct" or c["status"] != "ok" for c in calls)


async def test_agents_cannot_fetch_non_web_urls(stack, agent_run):
    """Agents have no unrestricted HTTP: fetch_page is the only outbound-looking capability and
    it refuses anything that is not an http(s) URL."""
    box = stack.gateway.bind(AgentName.RESEARCH, agent_run, correlation_id="i1")
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "mailto:a@b.example"):
        r = await box.call("fetch_page", {"url": bad})
        assert not r.ok, bad
        # It must be the capability/arg guard refusing, not an incidental audit failure.
        assert r.error_code != "audit_failed", bad


# ------------------------------------------------------------- audit and API readback


async def test_agent_runs_and_tool_calls_are_persisted_and_attributable(stack, seeded, campaign):
    discovery = await _discover(stack, campaign)
    run_id = discovery.record.agent_run_id
    async with seeded() as uow:
        run = (
            (await uow.session.execute(sa.select(t.agent_runs).where(t.agent_runs.c.id == run_id)))
            .mappings()
            .one()
        )
        calls = (
            (
                await uow.session.execute(
                    sa.select(t.tool_calls).where(t.tool_calls.c.agent_run_id == run_id)
                )
            )
            .mappings()
            .all()
        )
        audits = (
            (
                await uow.session.execute(
                    sa.select(t.audit_events).where(
                        t.audit_events.c.action == "prospect.discovered"
                    )
                )
            )
            .mappings()
            .all()
        )
    assert run["agent"] == "discovery" and run["failure"] is None
    assert run["finished_at"] is not None and run["structured_result"] is not None
    assert [c["tool_name"] for c in calls] == ["read_campaign_state", "search", "record_candidates"]
    assert audits, "discovering a prospect must be audited"
    assert all(a["actor_type"] == "agent" and a["agent_run_id"] == run_id for a in audits)


async def test_api_readback_explains_a_deferred_prospect(stack, seeded, services, campaign, api):
    prospect_id = await _make_prospect(seeded, campaign, DEFER_DOMAINS[0])
    out = await _slice(stack, seeded, services, campaign, prospect_id)
    assert out.outcome is QualificationOutcome.DEFERRED

    r = await api.get(f"/prospects/{prospect_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prospect"]["status"] == "DEFERRED"
    decision = body["qualification"]
    assert decision["outcome"] == "DEFERRED"
    assert decision["missing_metrics"]
    # An unavailable metric is null on the wire — never 0, never NaN.
    states = {m["metric"]: m for m in body["metrics"]}
    for metric in decision["missing_metrics"]:
        assert states[metric]["value"] is None
        assert states[metric]["state"] in {"unavailable", "never_fetched"}
    json.dumps(body, allow_nan=False)

    listed = await api.get(
        "/prospects", params={"status": "DEFERRED", "campaign_id": str(campaign)}
    )
    assert listed.status_code == 200
    assert str(prospect_id) in {p["id"] for p in listed.json()["items"]}
