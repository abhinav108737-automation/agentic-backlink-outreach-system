"""Kill switch and runtime settings, stored in `system_settings` and audited on every change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kb_contracts.events import Actor

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory

KILL_SWITCH_KEY = "kill_switch"


@dataclass(frozen=True)
class KillSwitchState:
    """`version == 0` means the row has never been written (switch is off)."""

    active: bool
    reason: str | None
    changed_by: str | None
    version: int


async def read_kill_switch(uow: UnitOfWork) -> KillSwitchState:
    row = await uow.settings.get(KILL_SWITCH_KEY)
    if row is None:
        return KillSwitchState(False, None, None, 0)
    value: dict[str, Any] = row["value"]
    return KillSwitchState(
        # Fail closed: a row that exists but is malformed counts as ACTIVE. Only a literal
        # `false` (or no row at all) means the switch is off.
        active=value.get("active", True) is not False,
        reason=value.get("reason"),
        changed_by=value.get("changed_by"),
        version=int(row["version"]),
    )


class SettingsService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get_kill_switch(self) -> KillSwitchState:
        async with self._uow_factory() as uow:
            return await read_kill_switch(uow)

    async def set_kill_switch(
        self,
        *,
        active: bool,
        reason: str | None,
        actor: Actor,
        correlation_id: str,
        expected_version: int | None = None,
        uow: UnitOfWork | None = None,
    ) -> KillSwitchState:
        """Flip the switch and write the audit row in one transaction. `expected_version`
        makes the change compare-and-set (`OptimisticLockError` on a stale view)."""
        if uow is not None:
            return await self._set(uow, active, reason, actor, correlation_id, expected_version)
        async with self._uow_factory() as owned:
            return await self._set(owned, active, reason, actor, correlation_id, expected_version)

    async def _set(
        self,
        uow: UnitOfWork,
        active: bool,
        reason: str | None,
        actor: Actor,
        correlation_id: str,
        expected_version: int | None,
    ) -> KillSwitchState:
        before = await read_kill_switch(uow)
        by = f"{actor.actor_type.value}:{actor.actor_id}"
        value = {"active": active, "reason": reason, "changed_by": by}
        version = await uow.settings.set(KILL_SWITCH_KEY, value, expected_version=expected_version)
        await AuditTrail(uow.audit).record(
            actor=actor,
            action="kill_switch.activated" if active else "kill_switch.deactivated",
            subject_type="system_setting",
            subject_id=None,
            correlation_id=correlation_id,
            old_state={"active": before.active, "version": before.version},
            new_state={"active": active, "version": version},
            reason=reason,
        )
        return KillSwitchState(active, reason, by, version)
