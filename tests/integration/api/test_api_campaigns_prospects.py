"""API behaviour against a real PostgreSQL: campaigns, transitions, prospects, null metrics."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from kb_contracts.enums import ProspectStatus

pytestmark = pytest.mark.db


async def new_campaign(client, **body):
    r = await client.post("/campaigns", json={"name": "Q4 outreach", **body})
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_get_list_campaign(client):
    c = await new_campaign(client, goals={"links": 10})
    assert c["status"] == "DRAFT" and c["version"] == 1
    assert (await client.get(f"/campaigns/{c['id']}")).json()["goals"] == {"links": 10}
    page = (await client.get("/campaigns")).json()
    assert [x["id"] for x in page["items"]] == [c["id"]] and page["next_cursor"] is None


async def test_campaign_validation_and_not_found_are_problem_json(client):
    r = await client.post("/campaigns", json={"name": ""})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/problem+json")
    r = await client.post("/campaigns", json={"name": "x", "bogus": 1})
    assert r.status_code == 422
    r = await client.get(f"/campaigns/{uuid4()}")
    assert r.status_code == 404 and r.json()["title"] == "Not Found"
    assert (await client.get("/campaigns/not-a-uuid")).status_code == 422
    r = await client.post("/campaigns", json={"name": "x", "policy_version_id": str(uuid4())})
    assert r.status_code == 422


async def test_idempotent_create_replays_without_duplicating(client):
    h = {"Idempotency-Key": "abc-1"}
    a = await client.post("/campaigns", json={"name": "Once"}, headers=h)
    b = await client.post("/campaigns", json={"name": "Once"}, headers=h)
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert b.headers["Idempotency-Replayed"] == "true" and "Idempotency-Replayed" not in a.headers
    assert len((await client.get("/campaigns")).json()["items"]) == 1
    r = await client.post("/campaigns", json={"name": "Different"}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_activate_pause_roundtrip_bumps_version_and_audits(client, seeded):
    c = await new_campaign(client)
    a = (await client.post(f"/campaigns/{c['id']}/activate", json={"reason": "go"})).json()
    assert (a["status"], a["version"]) == ("ACTIVE", 2)
    p = (await client.post(f"/campaigns/{c['id']}/pause")).json()
    assert (p["status"], p["version"]) == ("PAUSED", 3)
    run = (await client.post(f"/campaigns/{c['id']}/run")).json()
    assert run["status"] == "ACTIVE"
    from uuid import UUID

    async with seeded() as uow:
        rows = await uow.audit.list_for_subject("campaign", UUID(c["id"]))
    assert [r["action"] for r in rows] == [
        "campaign.created",
        "campaign.transition",
        "campaign.transition",
        "campaign.transition",
    ]
    assert rows[1]["reason"] == "go" and rows[1]["new_state"]["status"] == "ACTIVE"


async def test_illegal_campaign_transition_is_a_clean_409_not_a_500(client):
    c = await new_campaign(client)
    r = await client.post(f"/campaigns/{c['id']}/pause")  # DRAFT -> PAUSED is illegal
    assert r.status_code == 409
    body = r.json()
    assert r.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == "INVALID_TRANSITION"
    assert (body["from_state"], body["to_state"], body["machine"]) == (
        "DRAFT",
        "PAUSED",
        "campaign",
    )
    assert body["correlation_id"]
    await client.post(f"/campaigns/{c['id']}/activate")
    again = await client.post(f"/campaigns/{c['id']}/activate")  # ACTIVE -> ACTIVE
    assert again.status_code == 409 and again.json()["code"] == "INVALID_TRANSITION"
    assert (await client.get(f"/campaigns/{c['id']}")).json()["status"] == "ACTIVE"


async def test_unknown_campaign_actions_are_404(client):
    for path in ("pause", "activate", "run"):
        assert (await client.post(f"/campaigns/{uuid4()}/{path}")).status_code == 404


async def seed_prospects(seeded, campaign_id, specs):
    """specs: (domain, {metric: value|None}). Returns prospect ids. Setup goes via repositories."""
    from uuid import UUID

    ids = []
    async with seeded() as uow:
        for domain, metrics in specs:
            d = await uow.domains.get_or_create(domain)
            p = await uow.prospects.create(campaign_id=UUID(campaign_id), domain_id=d.id)
            for m, v in metrics.items():
                await uow.domain_metrics.record(
                    domain_id=d.id,
                    provider="t",
                    metric=m,
                    value=v,
                    source="t",
                    fetched_at=datetime.now(UTC),
                )
            ids.append(p.id)
    return ids


async def test_prospect_list_filter_and_cursor_pagination(client, seeded):
    c = await new_campaign(client)
    ids = await seed_prospects(seeded, c["id"], [(f"site{i}.com", {}) for i in range(5)])
    async with seeded() as uow:
        await uow.prospects.set_status(ids[0], ProspectStatus.DEFERRED, expected_version=1)
    seen, cursor = [], None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/prospects", params=params)).json()
        seen += [p["id"] for p in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [str(i) for i in ids]
    only = (await client.get("/prospects", params={"status": "DEFERRED"})).json()
    assert [p["id"] for p in only["items"]] == [str(ids[0])]
    assert (await client.get("/prospects", params={"status": "NOPE"})).status_code == 422
    assert (await client.get("/prospects", params={"cursor": "garbage"})).status_code == 400
    assert (await client.get("/prospects", params={"limit": 0})).status_code == 422
    none = await client.get("/prospects", params={"campaign_id": str(uuid4())})
    assert none.json()["items"] == []


async def test_prospect_detail_explains_deferred_state2_with_null_metric(client, seeded):
    c = await new_campaign(client)
    (pid,) = await seed_prospects(
        seeded, c["id"], [("unavail.com", {"domain_authority": None, "spam_score": 0.2})]
    )
    async with seeded() as uow:
        from kb_contracts.config import Settings
        from kb_core.audit.service import OPERATOR
        from kb_core.policy.engine import DeterministicPolicyEngine
        from kb_core.qualification.service import QualificationService
        from kb_core.scheduling.clock import FakeClock
        from kb_core.transitions.service import TransitionService

        pol = DeterministicPolicyEngine(seeded, Settings(_env_file=None))
        q = QualificationService(seeded, pol, TransitionService(seeded, FakeClock()))
        res = await q.qualify_in(uow, pid, actor=OPERATOR, correlation_id="t")
    assert res.prospect.status is ProspectStatus.DEFERRED
    r = await client.get(f"/prospects/{pid}")
    assert r.status_code == 200
    raw = r.text
    body = json.loads(raw)
    json.dumps(body, allow_nan=False)  # survives strict JSON
    assert "NaN" not in raw and "Infinity" not in raw
    q = body["qualification"]
    assert q["outcome"] == "DEFERRED" and q["missing_metrics"] == ["domain_authority"]
    assert q["metric_snapshot"] == {"domain_authority": None, "spam_score": 0.2}
    assert "METRIC_UNAVAILABLE:domain_authority" in q["reason_codes"]
    assert "unavailable" in body["qualification_explanation"]
    states = {m["metric"]: m for m in body["metrics"]}
    assert states["domain_authority"] == {
        "metric": "domain_authority",
        "state": "unavailable",
        "value": None,
    }
    assert states["spam_score"]["value"] == 0.2 and states["spam_score"]["state"] == "available"
    assert body["domain"]["registered_domain"] == "unavail.com"


async def test_prospect_detail_state3_never_fetched_is_distinct_and_null(client, seeded):
    c = await new_campaign(client)
    (pid,) = await seed_prospects(seeded, c["id"], [("never.com", {"spam_score": 0.2})])
    body = (await client.get(f"/prospects/{pid}")).json()
    json.dumps(body, allow_nan=False)
    states = {m["metric"]: m for m in body["metrics"]}
    assert states["domain_authority"] == {
        "metric": "domain_authority",
        "state": "never_fetched",
        "value": None,
    }
    assert body["qualification"] is None and body["ranking"] is None and body["contacts"] == []


async def test_prospect_detail_includes_ranking_dossier_evidence_contacts(client, seeded):
    c = await new_campaign(client)
    (pid,) = await seed_prospects(seeded, c["id"], [("full.com", {})])
    async with seeded() as uow:
        p = await uow.prospects.get(pid)
        pv = (await uow.policies.active_global_version()).id
        await uow.rankings.record(
            prospect_id=pid,
            policy_version_id=pv,
            score=0.7,
            components={"authority": 0.7},
            explanation="because",
        )
        dos = await uow.dossiers.create(
            prospect_id=pid, summary="S", topics=["seo"], editorial_signals={}
        )
        await uow.evidence.record_many(
            pid,
            [
                {
                    "source_url": "https://full.com/a",
                    "excerpt": "x",
                    "content_hash": "h",
                    "kind": "page",
                    "extracted_at": datetime.now(UTC),
                    "extractor": "t",
                }
            ],
            dossier_id=dos.id,
        )
        ct = await uow.contacts.create(
            domain_id=p.domain_id, name="Ed", role="editor", contact_type="person", confidence=0.9
        )
        await uow.contacts.add_channel(
            contact_id=ct.id, channel_type="email", value_normalized="ed@full.com", confidence=0.9
        )
    body = (await client.get(f"/prospects/{pid}")).json()
    assert body["ranking"]["explanation"] == "because"
    assert body["dossier"]["summary"] == "S" and len(body["evidence"]) == 1
    assert body["contacts"][0]["channels"][0]["value_normalized"] == "ed@full.com"


async def test_unknown_prospect_is_404_and_stubs_for_other_lanes_stay_501(client):
    assert (await client.get(f"/prospects/{uuid4()}")).status_code == 404
    assert (await client.post(f"/prospects/{uuid4()}/research")).status_code == 501
