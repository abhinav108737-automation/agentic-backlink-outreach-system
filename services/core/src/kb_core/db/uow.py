"""`SqlUnitOfWork`: one PostgreSQL transaction exposing all 18 repositories.

Atomicity guarantee (docs/13 enforcement rule 4): the unit of work owns exactly one
`AsyncSession`, hence one connection and one transaction. Every repository is constructed over
that same session and none of them ever commits. The only `COMMIT` is in `__aexit__` on a clean
exit, and any exception (including `asyncio.CancelledError`) takes the `ROLLBACK` path. So a
status change and the `audit_events` row written beside it become visible together or not at
all.

Repository calls are plain Core statements executed immediately on the transaction's
connection, so `flush()` has no buffered ORM state to push; it is kept (and does call
`Session.flush`) so the seam stays valid if a repository ever batches writes.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from kb_core.db.protocols import (
    AgentRunRepository,
    ApprovalRepository,
    AuditRepository,
    CampaignRepository,
    ContactRepository,
    DomainMetricRepository,
    DomainRepository,
    DossierRepository,
    EvidenceRepository,
    IdempotencyRepository,
    JobRepository,
    PolicyRepository,
    ProspectRepository,
    ProspectUrlRepository,
    QualificationRepository,
    RankingRepository,
    SettingsRepository,
    SuppressionRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
from kb_core.db.repos_core import (
    SqlAuditRepository,
    SqlCampaignRepository,
    SqlDomainMetricRepository,
    SqlDomainRepository,
    SqlPolicyRepository,
    SqlProspectRepository,
    SqlProspectUrlRepository,
)
from kb_core.db.repos_research import (
    SqlContactRepository,
    SqlDossierRepository,
    SqlEvidenceRepository,
    SqlQualificationRepository,
    SqlRankingRepository,
    SqlSuppressionRepository,
)
from kb_core.db.repos_runtime import (
    SqlAgentRunRepository,
    SqlApprovalRepository,
    SqlIdempotencyRepository,
    SqlJobRepository,
    SqlSettingsRepository,
)


class SqlUnitOfWork:
    # Annotated with the protocol types: protocol attributes are invariant, so declaring the
    # concrete classes here would make mypy reject the structural match.
    audit: AuditRepository
    policies: PolicyRepository
    campaigns: CampaignRepository
    domains: DomainRepository
    domain_metrics: DomainMetricRepository
    prospects: ProspectRepository
    prospect_urls: ProspectUrlRepository
    qualifications: QualificationRepository
    rankings: RankingRepository
    evidence: EvidenceRepository
    dossiers: DossierRepository
    contacts: ContactRepository
    suppressions: SuppressionRepository
    idempotency: IdempotencyRepository
    jobs: JobRepository
    approvals: ApprovalRepository
    settings: SettingsRepository
    agent_runs: AgentRunRepository

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._session: AsyncSession = sessions()
        self._entered = False
        s = self._session
        self.audit = SqlAuditRepository(s)
        self.policies = SqlPolicyRepository(s)
        self.campaigns = SqlCampaignRepository(s)
        self.domains = SqlDomainRepository(s)
        self.domain_metrics = SqlDomainMetricRepository(s)
        self.prospects = SqlProspectRepository(s)
        self.prospect_urls = SqlProspectUrlRepository(s)
        self.qualifications = SqlQualificationRepository(s)
        self.rankings = SqlRankingRepository(s)
        self.evidence = SqlEvidenceRepository(s)
        self.dossiers = SqlDossierRepository(s)
        self.contacts = SqlContactRepository(s)
        self.suppressions = SqlSuppressionRepository(s)
        self.idempotency = SqlIdempotencyRepository(s)
        self.jobs = SqlJobRepository(s)
        self.approvals = SqlApprovalRepository(s)
        self.settings = SqlSettingsRepository(s)
        self.agent_runs = SqlAgentRunRepository(s)

    @property
    def session(self) -> AsyncSession:
        """Escape hatch for data-layer code (seeding, rebuild) and tests. Not on the protocol,
        so services typed against `UnitOfWork` cannot reach it."""
        return self._session

    async def __aenter__(self) -> Self:
        if self._entered:
            raise RuntimeError("a SqlUnitOfWork is single-use; create one per transaction")
        self._entered = True
        await self._session.begin()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        except BaseException:
            await self._session.rollback()
            raise
        finally:
            await self._session.close()

    async def flush(self) -> None:
        await self._session.flush()


class SqlUnitOfWorkFactory:
    def __init__(self, engine: AsyncEngine) -> None:
        # expire_on_commit is irrelevant (no ORM instances) but False avoids surprise reloads.
        self._sessions = async_sessionmaker(engine, expire_on_commit=False, autobegin=False)

    def __call__(self) -> SqlUnitOfWork:
        return SqlUnitOfWork(self._sessions)


def create_uow_factory(engine: AsyncEngine) -> SqlUnitOfWorkFactory:
    return SqlUnitOfWorkFactory(engine)


# Static conformance checks: mypy (strict) fails the build if either class stops satisfying the
# frozen Wave-1 seam, instead of the match being assumed.
def _uow_satisfies_protocol(uow: SqlUnitOfWork) -> UnitOfWork:
    return uow


def _factory_satisfies_protocol(factory: SqlUnitOfWorkFactory) -> UnitOfWorkFactory:
    return factory
