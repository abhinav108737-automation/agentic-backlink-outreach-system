"""Drive the backlink machine from one verification pass (docs/13 section 4).

Every pass appends a `backlink_verifications` row (append-only in the database) carrying the
`verifier_version`, whatever the outcome. Transitions go through `TransitionPort`.

State handling per outcome:

  VERIFIED / CHANGED   misses reset to 0. EXPECTED -> FOUND -> outcome; FOUND -> outcome;
                       VERIFIED <-> CHANGED as the evidence flips; MISSING -> VERIFIED, or
                       MISSING -> FOUND -> CHANGED; LOST -> FOUND -> outcome (the table's audited
                       "link reappeared" edge).
  MISSING              consecutive_misses += 1. A live backlink becomes MISSING (misses = 1).
                       A MISSING backlink becomes LOST once misses >= `lost_after_misses`
                       (default 3, the threshold decided here, not in the repository). EXPECTED
                       stays EXPECTED during `expected_grace` after creation (publication lag).
  INCONCLUSIVE         row recorded, status and misses untouched: an unreachable host never
                       demotes a link.

Only VERIFIED is active (`evaluate.is_active`). MISSING and LOST are never counted as active.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from kb_contracts.domain import Backlink
from kb_contracts.enums import BacklinkStatus
from kb_contracts.events import Actor
from kb_contracts.providers import Clock, CrawlerProvider, ProviderError

from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.errors import EntityNotFound
from kb_core.outreach.ports import ACTOR_VERIFIER, VERIFIER_VERSION, TransitionPort
from kb_core.verification.evaluate import CheckResult, Outcome, is_active
from kb_core.verification.evaluate import evaluate as evaluate_page

DEFAULT_LOST_AFTER_MISSES = 3

_B = BacklinkStatus


@dataclass(frozen=True)
class VerificationResult:
    backlink: Backlink
    outcome: Outcome
    reasons: tuple[str, ...]
    status_before: BacklinkStatus
    consecutive_misses: int

    @property
    def active(self) -> bool:
        return is_active(self.backlink)


class VerificationService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        crawler: CrawlerProvider,
        transitions: TransitionPort,
        clock: Clock,
        *,
        lost_after_misses: int = DEFAULT_LOST_AFTER_MISSES,
        expected_grace: timedelta = timedelta(0),
    ) -> None:
        if lost_after_misses < 1:
            raise ValueError("lost_after_misses must be >= 1")
        self._uow_factory = uow_factory
        self._crawler = crawler
        self._tr = transitions
        self._clock = clock
        self._lost_after = lost_after_misses
        self._grace = expected_grace

    async def verify(
        self, backlink_id: UUID, *, correlation_id: str, actor: Actor = ACTOR_VERIFIER
    ) -> VerificationResult:
        async with self._uow_factory() as uow:
            backlink = await uow.backlinks.get(backlink_id)
        if backlink is None:
            raise EntityNotFound("backlink", backlink_id)

        check = await self._check(backlink)  # network-shaped work, no transaction held

        async with self._uow_factory() as uow:
            fresh = await uow.backlinks.get(backlink_id)
            assert fresh is not None
            return await self._apply(uow, fresh, check, correlation_id, actor)

    async def _check(self, backlink: Backlink) -> CheckResult:
        try:
            page = await self._crawler.fetch(backlink.source_url)
        except ProviderError as exc:
            return evaluate_page(None, backlink, fetch_error=type(exc).__name__)
        return evaluate_page(page, backlink)

    async def _apply(
        self, uow: UnitOfWork, b: Backlink, check: CheckResult, correlation_id: str, actor: Actor
    ) -> VerificationResult:
        now = self._clock.now()
        before = b.status
        link = check.link
        await uow.backlinks.record_verification(
            backlink_id=b.id,
            checked_at=now,
            link_found=link is not None,
            result=check.outcome.value,
            verifier_version=VERIFIER_VERSION,
            http_status=check.http_status,
            final_url=check.final_url,
            canonical_url=check.canonical_url,
            anchor_text=link.anchor_text if link else None,
            rel_attrs=link.rel if link else (),
            context_excerpt=link.context if link else None,
        )
        why = f"{check.outcome.value}: {','.join(check.reasons) or 'all checks passed'}"

        async def go(entity: Backlink, target: BacklinkStatus) -> Backlink:
            moved = await self._tr.transition_in(
                uow, entity, target, actor=actor, reason=why, correlation_id=correlation_id
            )
            result: Backlink = moved.entity
            return result

        misses = b.consecutive_misses
        if check.outcome is Outcome.INCONCLUSIVE:
            await uow.backlinks.set_miss_counters(
                b.id, consecutive_misses=misses, seen=False, checked_at=now
            )
        elif check.outcome in (Outcome.VERIFIED, Outcome.CHANGED):
            misses = 0
            await uow.backlinks.set_miss_counters(
                b.id, consecutive_misses=0, seen=True, checked_at=now
            )
            target = _B.VERIFIED if check.outcome is Outcome.VERIFIED else _B.CHANGED
            if b.status is _B.MISSING and target is _B.VERIFIED:
                b = await go(b, _B.VERIFIED)
            else:
                if b.status in (_B.EXPECTED, _B.MISSING, _B.LOST):
                    b = await go(b, _B.FOUND)
                if b.status is not target:
                    b = await go(b, target)
        else:  # MISSING
            if (
                b.status is _B.EXPECTED
                and self._grace > timedelta(0)
                and now - b.created_at < self._grace
            ):
                await uow.backlinks.set_miss_counters(
                    b.id, consecutive_misses=0, seen=False, checked_at=now
                )
                misses = 0
            else:
                misses = b.consecutive_misses + 1 if b.status in (_B.MISSING, _B.LOST) else 1
                await uow.backlinks.set_miss_counters(
                    b.id, consecutive_misses=misses, seen=False, checked_at=now
                )
                if b.status not in (_B.MISSING, _B.LOST):
                    b = await go(b, _B.MISSING)
                if b.status is _B.MISSING and misses >= self._lost_after:
                    b = await go(b, _B.LOST)
        final = (await uow.backlinks.get(b.id)) or b
        return VerificationResult(final, check.outcome, check.reasons, before, misses)
