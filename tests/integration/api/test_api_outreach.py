"""I2 outreach endpoints and send-path guarantees against the real cloned PostgreSQL."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from kb_contracts.enums import MessageStatus, SequenceStatus, SuppressionReason, SuppressionScope
from kb_core.approvals.service import ApprovalService
from kb_core.audit.service import OPERATOR
from kb_core.db import repos_core
from kb_core.scheduling.clock import FakeClock
from kb_core.sending.service import BeginOutcome, SendService
from kb_core.suppression.service import SuppressionService
from kb_core.transitions.service import TransitionService

pytestmark = pytest.mark.db

M, S = MessageStatus, SequenceStatus


async def make_world(client, seeded, *, status=M.QA_PASSED, sender=True, step_no=1):
    c = (await client.post("/campaigns", json={"name": "c"})).json()
    assert (await client.post(f"/campaigns/{c['id']}/activate")).status_code == 200
    async with seeded() as uow:
        if sender:
            acct = (
                await uow.session.execute(
                    sa.text(
                        "INSERT INTO provider_accounts (provider_type, provider_name, enabled) "
                        "VALUES ('email','mock',true) RETURNING id"
                    )
                )
            ).scalar_one()
            await uow.session.execute(
                sa.text("UPDATE campaigns SET sender_provider_account_id=:a WHERE id=:c"),
                {"a": acct, "c": UUID(c["id"])},
            )
        d = await uow.domains.get_or_create("example.com")
        p = await uow.prospects.create(campaign_id=UUID(c["id"]), domain_id=d.id)
        contact = await uow.contacts.create(
            domain_id=d.id, name=None, role=None, contact_type="editor", confidence=0.9
        )
        ch = await uow.contacts.add_channel(
            contact_id=contact.id,
            channel_type="email",
            value_normalized="editor@example.com",
            confidence=0.9,
        )
        seq = await uow.sequences.create(
            campaign_id=UUID(c["id"]), prospect_id=p.id, contact_channel_id=ch.id
        )
        msg = await uow.messages.create(
            sequence_id=seq.id,
            step_no=step_no,
            subject="Hi",
            body="Body text",
            idempotency_key=f"idem-{uuid4()}",
        )
        for st in _path(status):
            msg = await uow.messages.set_status(msg.id, st, expected_version=msg.version)
    return {"campaign": c, "prospect": p, "channel": ch, "sequence": seq, "message": msg}


def _path(target):
    order = [M.QA_PASSED, M.PENDING_APPROVAL, M.APPROVED, M.QUEUED, M.SENDING]
    if target is M.DRAFT:
        return []
    return [M.QA_PASSED] if target is M.QA_PASSED else order[: order.index(target) + 1]


async def jobs(seeded):
    async with seeded() as uow:
        rows = await uow.session.execute(
            sa.text(
                "select id, job_type, status, concurrency_key, payload, subject_type, subject_id "
                "from scheduled_jobs"
            )
        )
        return [dict(r) for r in rows.mappings().all()]


async def test_approve_then_send_enqueues_and_never_sends(client, seeded):
    w = await make_world(client, seeded)
    mid = str(w["message"].id)
    a = await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED", "reason": "ok"})
    assert a.status_code == 200 and a.json()["status"] == "APPROVED"

    s = await client.post(f"/messages/{mid}/send", headers={"Idempotency-Key": "send-1"})
    assert s.status_code == 202
    body = s.json()
    assert body["job_type"] == "outreach.send" and body["message"]["status"] == "QUEUED"
    assert body["replayed"] is False

    (job,) = await jobs(seeded)
    assert str(job["id"]) == body["job_id"] and job["status"] == "SCHEDULED"
    assert job["concurrency_key"] == f"sequence:{w['sequence'].id}"
    assert job["subject_type"] == "sequence"
    assert set(job["payload"]) == {"message_id", "sequence_id", "correlation_id"}
    got = (await client.get(f"/messages/{mid}")).json()
    assert got["status"] == "QUEUED"  # not SENDING / SENT: nothing was delivered inline

    again = await client.post(f"/messages/{mid}/send", headers={"Idempotency-Key": "send-1"})
    assert again.status_code == 202 and again.json()["job_id"] == body["job_id"]
    assert again.headers["Idempotency-Replayed"] == "true" and again.json()["replayed"] is True
    assert len(await jobs(seeded)) == 1

    reuse = await client.post(f"/messages/{uuid4()}/send", headers={"Idempotency-Key": "send-1"})
    assert reuse.status_code == 422 and reuse.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_send_is_refused_with_block_codes_when_policy_blocks(client, seeded):
    w = await make_world(client, seeded, sender=False)  # fresh DB: no sender => SENDER_INVALID
    mid = str(w["message"].id)
    await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"})
    r = await client.post(f"/messages/{mid}/send")
    assert r.status_code == 409 and r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "POLICY_BLOCKED" and "SENDER_INVALID" in r.json()["block_codes"]
    assert await jobs(seeded) == []
    assert (await client.get(f"/messages/{mid}")).json()["status"] == "APPROVED"


async def test_kill_switch_blocks_the_send_endpoint(client, seeded):
    w = await make_world(client, seeded)
    mid = str(w["message"].id)
    await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"})
    await client.put("/system/kill-switch", json={"active": True, "reason": "stop"})
    r = await client.post(f"/messages/{mid}/send")
    assert r.status_code == 409 and "KILL_SWITCH_ACTIVE" in r.json()["block_codes"]
    assert await jobs(seeded) == []


async def test_send_before_approval_is_an_illegal_transition_not_a_500(client, seeded):
    w = await make_world(client, seeded)
    r = await client.post(f"/messages/{w['message'].id}/send")
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"


async def test_reject_and_bad_inputs(client, seeded):
    w = await make_world(client, seeded)
    mid = str(w["message"].id)
    for bad in ({"decision": "PENDING"}, {"decision": "NOPE"}, {}):
        assert (await client.post(f"/messages/{mid}/approve", json=bad)).status_code == 422
    r = await client.post(f"/messages/{mid}/approve", json={"decision": "REJECTED", "reason": "no"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert (await client.post(f"/messages/{uuid4()}/send")).status_code == 404
    assert (
        await client.post(f"/messages/{uuid4()}/approve", json={"decision": "APPROVED"})
    ).status_code == 404
    assert (await client.post("/messages/not-a-uuid/send")).status_code == 422


async def test_approve_replay_is_idempotent(client, seeded):
    w = await make_world(client, seeded)
    mid = str(w["message"].id)
    h = {"Idempotency-Key": "ap-1"}
    one = await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"}, headers=h)
    two = await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"}, headers=h)
    assert one.status_code == two.status_code == 200
    assert two.headers["Idempotency-Replayed"] == "true"
    assert len((await client.get("/approvals")).json()["items"]) == 0  # decided, none pending


async def test_readbacks_and_thread(client, seeded):
    w = await make_world(client, seeded)
    seq, mid = w["sequence"], str(w["message"].id)
    async with seeded() as uow:
        th = await uow.threads.get_or_create_for_sequence(seq.id, subject="Hi")
        await uow.replies.ingest(
            sequence_id=seq.id,
            thread_id=th["id"],
            provider_message_id="pm-1",
            from_normalized="editor@example.com",
            received_at=datetime(2026, 1, 1, tzinfo=UTC),
            body_sanitized="thanks",
            raw_hash="h",
        )
    t = await client.get(f"/threads/{th['id']}")
    assert t.status_code == 200
    tv = t.json()
    assert tv["sequence_id"] == str(seq.id) and [m["id"] for m in tv["messages"]] == [mid]
    assert [r["body_sanitized"] for r in tv["replies"]] == ["thanks"]
    assert (await client.get(f"/threads/{uuid4()}")).status_code == 404

    cid = w["campaign"]["id"]
    lst = await client.get(f"/campaigns/{cid}/sequences")
    assert [s["id"] for s in lst.json()["items"]] == [str(seq.id)]
    assert (await client.get(f"/campaigns/{cid}/sequences/{seq.id}")).status_code == 200
    assert (await client.get(f"/campaigns/{uuid4()}/sequences")).status_code == 404
    other = (await client.post("/campaigns", json={"name": "o"})).json()["id"]
    assert (await client.get(f"/campaigns/{other}/sequences/{seq.id}")).status_code == 404
    assert (await client.get(f"/messages?sequence_id={seq.id}")).json()["items"][0]["id"] == mid
    assert (await client.get(f"/messages?sequence_id={uuid4()}")).status_code == 404


async def test_opportunity_and_backlink_endpoints(client, seeded):
    w = await make_world(client, seeded)
    async with seeded() as uow:
        opp = await uow.opportunities.create(prospect_id=w["prospect"].id, sequence_id=None)
        bl = await uow.backlinks.create(
            opportunity_id=opp.id,
            source_url="https://example.com/a",
            target_url="https://ken.test/",
        )
    assert [o["id"] for o in (await client.get("/prospects/opportunities")).json()["items"]] == [
        str(opp.id)
    ]
    assert (await client.get(f"/prospects/opportunities/{opp.id}")).json()["status"] == "OPEN"
    assert (await client.get(f"/prospects/opportunities/{uuid4()}")).status_code == 404
    assert (await client.get(f"/prospects/{w['prospect'].id}")).status_code == 200  # not shadowed

    assert [b["id"] for b in (await client.get("/backlinks")).json()["items"]] == [str(bl.id)]
    detail = (await client.get(f"/backlinks/{bl.id}")).json()
    assert detail["backlink"]["status"] == "EXPECTED" and detail["verifications"] == []

    v = await client.post(f"/backlinks/{bl.id}/verify", headers={"Idempotency-Key": "v-1"})
    assert v.status_code == 202 and v.json()["job_type"] == "backlink.verify"
    again = await client.post(f"/backlinks/{bl.id}/verify", headers={"Idempotency-Key": "v-1"})
    assert again.json()["job_id"] == v.json()["job_id"] and again.json()["replayed"] is True
    (job,) = await jobs(seeded)
    assert job["job_type"] == "backlink.verify" and job["payload"]["backlink_id"] == str(bl.id)
    assert (await client.post(f"/backlinks/{uuid4()}/verify")).status_code == 404


# -- service-level guarantees on the real database ---------------------------------------------
def services(factory):
    clock = FakeClock()
    from kb_contracts.config import Settings
    from kb_core.policy.engine import DeterministicPolicyEngine

    tr = TransitionService(factory, clock)
    policy = DeterministicPolicyEngine(factory, Settings(_env_file=None), clock)
    return SendService(factory, policy, tr, ApprovalService(factory), clock), tr


async def test_pre_send_recheck_catches_a_suppression_added_after_approval(client, seeded):
    w = await make_world(client, seeded)
    mid = w["message"].id
    await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"})
    assert (await client.post(f"/messages/{mid}/send")).status_code == 202  # passes at enqueue
    async with seeded() as uow:  # ... then the address opts out
        await SuppressionService().add(
            uow,
            scope=SuppressionScope.EMAIL,
            value="editor@example.com",
            reason=SuppressionReason.OPT_OUT,
            actor=OPERATOR,
            correlation_id="s",
        )
    send, _ = services(seeded)
    res = await send.begin_send(mid, correlation_id="w")
    assert res.outcome is BeginOutcome.BLOCKED
    async with seeded() as uow:
        assert (await uow.messages.get(mid)).status is M.POLICY_BLOCKED


async def test_begin_send_claims_once_and_reports_reconcile_after_a_crash(client, seeded):
    w = await make_world(client, seeded)
    mid = w["message"].id
    await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"})
    await client.post(f"/messages/{mid}/send")
    async with seeded() as uow:
        seq = w["sequence"]
        await uow.sequences.set_status(seq.id, S.READY, expected_version=seq.version)
    send, _ = services(seeded)
    first = await send.begin_send(mid, correlation_id="w")
    assert first.outcome is BeginOutcome.PROCEED and first.message.status is M.SENDING
    # the process dies here; the retried job finds SENDING and must reconcile, not resend
    assert (await send.begin_send(mid, correlation_id="w")).outcome is BeginOutcome.RECONCILE
    done = await send.complete_send(mid, provider_message_id="prov-1", correlation_id="w")
    assert done.status is M.SENT
    assert (await send.begin_send(mid, correlation_id="w")).outcome is BeginOutcome.ALREADY_SENT
    async with seeded() as uow:
        assert (await uow.messages.get_by_idempotency_key(done.idempotency_key)).id == mid
        after = await uow.sequences.get(w["sequence"].id)
        assert after.current_step == 1 and after.status is S.WAITING_REPLY


@pytest.mark.parametrize("terminal", [S.OPTED_OUT, S.BOUNCED])
async def test_terminal_sequence_is_atomic_with_job_cancel_and_suppression(
    client, seeded, monkeypatch, terminal
):
    w = await make_world(client, seeded)
    mid = w["message"].id
    await client.post(f"/messages/{mid}/approve", json={"decision": "APPROVED"})
    await client.post(f"/messages/{mid}/send")  # QUEUED + a SCHEDULED outreach.send job
    async with seeded() as uow:
        seq = await uow.sequences.get(w["sequence"].id)
        for st in (S.READY, S.WAITING_REPLY):
            seq = await uow.sequences.set_status(seq.id, st, expected_version=seq.version)
    _, tr = services(seeded)

    real = repos_core.SqlAuditRepository.append

    async def flaky(self, **kw):
        if kw["action"] == "outreach_sequence.transition":
            raise RuntimeError("injected audit failure")
        return await real(self, **kw)

    monkeypatch.setattr(repos_core.SqlAuditRepository, "append", flaky)
    with pytest.raises(RuntimeError):
        await tr.transition(seq, terminal, actor=OPERATOR, reason="inject", correlation_id="x")
    monkeypatch.setattr(repos_core.SqlAuditRepository, "append", real)

    # nothing survived the failed transaction
    async with seeded() as uow:
        assert (await uow.sequences.get(seq.id)).status is S.WAITING_REPLY
        assert (await uow.messages.get(mid)).status is M.QUEUED
        assert (
            await uow.suppressions.matching(values={SuppressionScope.EMAIL: "editor@example.com"})
            == []
        )
    assert [j["status"] for j in await jobs(seeded)] == ["SCHEDULED"]

    # the same transition without the fault applies all three effects together
    await tr.transition(seq, terminal, actor=OPERATOR, reason="real", correlation_id="y")
    async with seeded() as uow:
        assert (await uow.sequences.get(seq.id)).status is terminal
        assert (await uow.messages.get(mid)).status is M.CANCELLED
        found = await uow.suppressions.matching(
            values={SuppressionScope.EMAIL: "editor@example.com"}
        )
        assert len(found) == 1
    assert [j["status"] for j in await jobs(seeded)] == ["CANCELLED"]
