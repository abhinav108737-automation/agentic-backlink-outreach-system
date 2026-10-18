"""`JobQueue` (C5) over `JobRepository`. PostgreSQL is the queue (ADR-0008/0017)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kb_core.db.protocols import UnitOfWorkFactory


class UowJobQueue:
    """Each call is its own unit of work. Idempotent on `idempotency_key`: enqueueing twice
    returns the same job id and never creates a duplicate."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def enqueue(
        self,
        job_type: str,
        payload: dict[str, Any],
        run_at: datetime,
        idempotency_key: str,
        concurrency_key: str | None = None,
    ) -> str:
        if not job_type.strip() or not idempotency_key.strip():
            raise ValueError("job_type and idempotency_key must not be empty")
        async with self._uow_factory() as uow:
            job = await uow.jobs.enqueue(
                job_type=job_type,
                payload=payload,
                run_at=run_at,  # naive datetimes are rejected by the repository
                idempotency_key=idempotency_key,
                concurrency_key=concurrency_key,
            )
            return str(job.id)

    async def cancel(self, idempotency_key: str) -> bool:
        async with self._uow_factory() as uow:
            return await uow.jobs.cancel(idempotency_key=idempotency_key)
