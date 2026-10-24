"""Approvals: create, list, decide. Every change is audited in the same transaction."""

from __future__ import annotations

from uuid import UUID

from kb_contracts.domain import ApprovalRequest
from kb_contracts.enums import ApprovalKind, ApprovalStatus
from kb_contracts.events import Actor

from kb_core.audit.service import AuditTrail
from kb_core.db.protocols import Page, UnitOfWork, UnitOfWorkFactory
from kb_core.db.repos_runtime import ApprovalAlreadyDecided
from kb_core.errors import DomainConflict, EntityNotFound, InvalidInput

_DECIDABLE = (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED)


class ApprovalService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def create_in(
        self,
        uow: UnitOfWork,
        *,
        kind: ApprovalKind,
        subject_type: str,
        subject_id: UUID,
        requested_by: Actor,
        reason: str | None,
        correlation_id: str,
    ) -> ApprovalRequest:
        if not subject_type.strip():
            raise InvalidInput("subject_type must not be empty")
        approval = await uow.approvals.create(
            kind=kind.value,
            subject_type=subject_type,
            subject_id=subject_id,
            requested_by=f"{requested_by.actor_type.value}:{requested_by.actor_id}",
            reason=reason,
            correlation_id=correlation_id,
        )
        await AuditTrail(uow.audit).record(
            actor=requested_by,
            action="approval.requested",
            subject_type="approval_request",
            subject_id=approval.id,
            correlation_id=correlation_id,
            new_state={
                "kind": kind.value,
                "subject_type": subject_type,
                "subject_id": str(subject_id),
                "status": approval.status.value,
            },
            reason=reason,
        )
        return approval

    async def create(
        self,
        *,
        kind: ApprovalKind,
        subject_type: str,
        subject_id: UUID,
        requested_by: Actor,
        reason: str | None,
        correlation_id: str,
    ) -> ApprovalRequest:
        async with self._uow_factory() as uow:
            return await self.create_in(
                uow,
                kind=kind,
                subject_type=subject_type,
                subject_id=subject_id,
                requested_by=requested_by,
                reason=reason,
                correlation_id=correlation_id,
            )

    async def decide(
        self,
        approval_id: UUID,
        *,
        status: ApprovalStatus,
        decided_by: Actor,
        reason: str | None,
        correlation_id: str,
    ) -> ApprovalRequest:
        async with self._uow_factory() as uow:
            return await self.decide_in(
                uow,
                approval_id,
                status=status,
                decided_by=decided_by,
                reason=reason,
                correlation_id=correlation_id,
            )

    async def decide_in(
        self,
        uow: UnitOfWork,
        approval_id: UUID,
        *,
        status: ApprovalStatus,
        decided_by: Actor,
        reason: str | None,
        correlation_id: str,
    ) -> ApprovalRequest:
        """Decide inside the caller's unit of work, so a decision and the transition it
        authorises (for example PENDING_APPROVAL -> APPROVED) commit or roll back together."""
        if status not in _DECIDABLE:
            raise InvalidInput("an approval is decided as APPROVED or REJECTED")
        before = await uow.approvals.get(approval_id)
        if before is None:
            raise EntityNotFound("approval", approval_id)
        try:
            decided = await uow.approvals.decide(
                approval_id,
                status=status,
                decided_by=f"{decided_by.actor_type.value}:{decided_by.actor_id}",
                reason=reason,
            )
        except ApprovalAlreadyDecided as exc:
            raise DomainConflict("APPROVAL_ALREADY_DECIDED", str(exc)) from exc
        await AuditTrail(uow.audit).record(
            actor=decided_by,
            action=f"approval.{status.value.lower()}",
            subject_type="approval_request",
            subject_id=approval_id,
            correlation_id=correlation_id,
            old_state={"status": before.status.value},
            new_state={"status": decided.status.value, "kind": decided.kind.value},
            reason=reason,
        )
        return decided

    async def get(self, approval_id: UUID) -> ApprovalRequest:
        async with self._uow_factory() as uow:
            found = await uow.approvals.get(approval_id)
            if found is None:
                raise EntityNotFound("approval", approval_id)
            return found

    async def list_pending(
        self, *, cursor: str | None = None, limit: int = 50
    ) -> Page[ApprovalRequest]:
        async with self._uow_factory() as uow:
            return await uow.approvals.list_pending(cursor=cursor, limit=limit)
