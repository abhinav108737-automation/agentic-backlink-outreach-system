import json
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.db


async def test_approval_create_list_decide_and_audit(client, seeded):
    sid = str(uuid4())
    r = await client.post(
        "/approvals",
        json={
            "kind": "message_send",
            "subject_type": "message",
            "subject_id": sid,
            "reason": "check",
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "PENDING"
    aid = r.json()["id"]
    assert [a["id"] for a in (await client.get("/approvals")).json()["items"]] == [aid]
    d = await client.post(f"/approvals/{aid}/decide", json={"decision": "APPROVED", "reason": "ok"})
    assert d.status_code == 200 and d.json()["status"] == "APPROVED"
    assert (await client.get("/approvals")).json()["items"] == []
    async with seeded() as uow:
        rows = await uow.audit.list_for_subject("approval_request", UUID(aid))
    assert [x["action"] for x in rows] == ["approval.requested", "approval.approved"]


async def test_double_decision_is_409_and_bad_inputs_are_clean(client):
    r = await client.post(
        "/approvals",
        json={"kind": "l3_policy_change", "subject_type": "policy", "subject_id": str(uuid4())},
    )
    aid = r.json()["id"]
    await client.post(f"/approvals/{aid}/decide", json={"decision": "REJECTED"})
    again = await client.post(f"/approvals/{aid}/decide", json={"decision": "APPROVED"})
    assert again.status_code == 409 and again.json()["code"] == "APPROVAL_ALREADY_DECIDED"
    for bad in ({"decision": "PENDING"}, {"decision": "EXPIRED"}):
        fresh = await client.post(
            "/approvals",
            json={"kind": "message_send", "subject_type": "m", "subject_id": str(uuid4())},
        )
        resp = await client.post(f"/approvals/{fresh.json()['id']}/decide", json=bad)
        assert resp.status_code == 422
    assert (
        await client.post(f"/approvals/{uuid4()}/decide", json={"decision": "APPROVED"})
    ).status_code == 404
    assert (
        await client.post(
            "/approvals", json={"kind": "nope", "subject_type": "m", "subject_id": str(uuid4())}
        )
    ).status_code == 422


async def test_settings_view_and_real_send_cannot_be_changed_here(client):
    s = (await client.get("/settings")).json()
    assert s["real_send_enabled"] is False and s["autonomy_mode"] == "shadow"
    assert s["kill_switch"] == {"active": False, "reason": None, "changed_by": None, "version": 0}
    for forbidden in ({"real_send_enabled": True}, {"autonomy_mode": "autonomous"}):
        assert (await client.put("/settings", json=forbidden)).status_code == 422
    assert (await client.get("/settings")).json()["real_send_enabled"] is False


async def test_kill_switch_via_system_endpoint_is_audited_and_visible(client, seeded):
    r = await client.put("/system/kill-switch", json={"active": True, "reason": "incident"})
    assert r.status_code == 200 and r.json()["active"] is True and r.json()["version"] == 1
    assert (await client.get("/system/health")).json()["status"] == "halted"
    assert (await client.get("/settings")).json()["kill_switch"]["reason"] == "incident"
    async with seeded() as uow:
        rows = [a for a in await _audit(uow) if a["action"].startswith("kill_switch")]
    assert len(rows) == 1 and rows[0]["actor_id"] == "operator" and rows[0]["reason"] == "incident"
    assert rows[0]["correlation_id"]
    off = await client.put("/system/kill-switch", json={"active": False})
    assert off.json()["active"] is False
    assert (await client.get("/system/health")).json()["status"] == "ok"


async def _audit(uow):
    from sqlalchemy import text

    res = await uow.session.execute(
        text("select action, actor_id, reason, correlation_id from audit_events")
    )
    return [dict(r) for r in res.mappings().all()]


async def test_kill_switch_via_settings_put_and_stale_version_conflict(client):
    r = await client.put("/settings", json={"kill_switch": {"active": True, "expected_version": 0}})
    assert r.status_code == 200 and r.json()["kill_switch"]["active"] is True
    stale = await client.put(
        "/settings", json={"kill_switch": {"active": False, "expected_version": 0}}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert (await client.get("/system/kill-switch")).json()["active"] is True


async def test_kill_switch_makes_policy_block_in_the_real_database(client, seeded):
    from kb_contracts.config import Settings
    from kb_contracts.policy import BlockCode, PolicyCheckRequest
    from kb_core.policy.engine import DeterministicPolicyEngine

    c = (await client.post("/campaigns", json={"name": "k"})).json()
    await client.post(f"/campaigns/{c['id']}/activate")
    engine = DeterministicPolicyEngine(seeded, Settings(_env_file=None))
    req = PolicyCheckRequest(
        action="follow_up",
        campaign_id=UUID(c["id"]),
        subject_type="message",
        subject_id=uuid4(),
        correlation_id="c",
        context={"delivery_mode": "mock"},
    )
    assert BlockCode.KILL_SWITCH_ACTIVE not in (await engine.check(req)).block_codes
    await client.put("/system/kill-switch", json={"active": True})
    d = await engine.check(req)
    assert BlockCode.KILL_SWITCH_ACTIVE in d.block_codes and not d.allowed


async def test_new_routes_require_the_token_and_payloads_are_strict_json(client):
    import httpx

    anon = httpx.AsyncClient(transport=client._transport, base_url="http://127.0.0.1")
    for method, path in [
        ("GET", "/campaigns"),
        ("PUT", "/system/kill-switch"),
        ("POST", "/approvals"),
        ("GET", "/system/health"),
    ]:
        assert (await anon.request(method, path)).status_code == 401
    await anon.aclose()
    json.dumps((await client.get("/settings")).json(), allow_nan=False)
