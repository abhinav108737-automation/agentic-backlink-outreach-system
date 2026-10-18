from uuid import uuid4

import pytest
from kb_contracts.enums import ApprovalKind, ApprovalStatus
from kb_core.approvals.service import ApprovalService
from kb_core.audit.service import OPERATOR
from kb_core.errors import DomainConflict, EntityNotFound, InvalidInput
from kb_core.testing.fakes import FakeUowFactory


async def make(svc):
    return await svc.create(
        kind=ApprovalKind.MESSAGE_SEND,
        subject_type="message",
        subject_id=uuid4(),
        requested_by=OPERATOR,
        reason="why",
        correlation_id="c1",
    )


async def test_create_list_decide_are_audited():
    f = FakeUowFactory()
    svc = ApprovalService(f)
    a = await make(svc)
    assert a.status is ApprovalStatus.PENDING
    assert [x.id for x in (await svc.list_pending()).items] == [a.id]
    done = await svc.decide(
        a.id, status=ApprovalStatus.APPROVED, decided_by=OPERATOR, reason="ok", correlation_id="c2"
    )
    assert done.status is ApprovalStatus.APPROVED and done.decided_by == "operator:operator"
    assert (await svc.list_pending()).items == ()
    acts = [r["action"] for r in f.store.audit]
    assert acts == ["approval.requested", "approval.approved"]
    assert f.store.audit[1]["old_state"] == {"status": "PENDING"}


async def test_decision_is_final():
    f = FakeUowFactory()
    svc = ApprovalService(f)
    a = await make(svc)
    await svc.decide(
        a.id, status=ApprovalStatus.REJECTED, decided_by=OPERATOR, reason=None, correlation_id="c"
    )
    with pytest.raises(DomainConflict) as e:
        await svc.decide(
            a.id,
            status=ApprovalStatus.APPROVED,
            decided_by=OPERATOR,
            reason=None,
            correlation_id="c",
        )
    assert e.value.code == "APPROVAL_ALREADY_DECIDED"
    assert (await svc.get(a.id)).status is ApprovalStatus.REJECTED
    assert len([r for r in f.store.audit if r["action"].startswith("approval.")]) == 2


@pytest.mark.parametrize("bad", [ApprovalStatus.PENDING, ApprovalStatus.EXPIRED])
async def test_only_approved_or_rejected_can_be_decided(bad):
    svc = ApprovalService(FakeUowFactory())
    a = await make(svc)
    with pytest.raises(InvalidInput):
        await svc.decide(a.id, status=bad, decided_by=OPERATOR, reason=None, correlation_id="c")


async def test_unknown_approval_is_not_found_and_blank_subject_rejected():
    svc = ApprovalService(FakeUowFactory())
    with pytest.raises(EntityNotFound):
        await svc.decide(
            uuid4(),
            status=ApprovalStatus.APPROVED,
            decided_by=OPERATOR,
            reason=None,
            correlation_id="c",
        )
    with pytest.raises(EntityNotFound):
        await svc.get(uuid4())
    with pytest.raises(InvalidInput):
        await svc.create(
            kind=ApprovalKind.MESSAGE_SEND,
            subject_type=" ",
            subject_id=uuid4(),
            requested_by=OPERATOR,
            reason=None,
            correlation_id="c",
        )


async def test_pagination_cursor():
    svc = ApprovalService(FakeUowFactory())
    ids = [(await make(svc)).id for _ in range(3)]
    p1 = await svc.list_pending(limit=2)
    p2 = await svc.list_pending(cursor=p1.next_cursor, limit=2)
    assert [a.id for a in p1.items] + [a.id for a in p2.items] == ids and p2.next_cursor is None
