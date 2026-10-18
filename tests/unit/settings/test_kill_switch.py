import pytest
from kb_core.audit.service import OPERATOR
from kb_core.db.protocols import OptimisticLockError
from kb_core.settings.service import SettingsService
from kb_core.testing.fakes import FakeUowFactory


async def test_default_is_off_and_version_zero():
    s = await SettingsService(FakeUowFactory()).get_kill_switch()
    assert (s.active, s.version) == (False, 0)


async def test_flip_is_audited_with_actor_reason_and_before_after():
    f = FakeUowFactory()
    svc = SettingsService(f)
    on = await svc.set_kill_switch(
        active=True, reason="incident", actor=OPERATOR, correlation_id="c1"
    )
    off = await svc.set_kill_switch(
        active=False, reason="resolved", actor=OPERATOR, correlation_id="c2"
    )
    assert (on.active, on.version, off.active, off.version) == (True, 1, False, 2)
    rows = f.store.audit
    assert [r["action"] for r in rows] == ["kill_switch.activated", "kill_switch.deactivated"]
    assert rows[0]["old_state"] == {"active": False, "version": 0}
    assert rows[0]["new_state"] == {"active": True, "version": 1}
    assert rows[0]["reason"] == "incident" and rows[0]["actor_id"] == "operator"
    assert rows[1]["correlation_id"] == "c2"


async def test_compare_and_set_rejects_stale_view_and_writes_no_audit():
    f = FakeUowFactory()
    svc = SettingsService(f)
    await svc.set_kill_switch(active=True, reason=None, actor=OPERATOR, correlation_id="c")
    with pytest.raises(OptimisticLockError):
        await svc.set_kill_switch(
            active=False, reason=None, actor=OPERATOR, correlation_id="c", expected_version=0
        )
    assert (await svc.get_kill_switch()).active is True
    assert len(f.store.audit) == 1


async def test_audit_failure_rolls_the_flip_back():
    f = FakeUowFactory()
    svc = SettingsService(f)
    f.store.fail_audit = True
    with pytest.raises(RuntimeError):
        await svc.set_kill_switch(active=True, reason=None, actor=OPERATOR, correlation_id="c")
    f.store.fail_audit = False
    assert (await svc.get_kill_switch()).active is False
