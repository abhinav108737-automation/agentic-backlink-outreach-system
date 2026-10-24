"""Read-only drafting context and the deterministic template producer.

A `DraftProducer` turns a `DraftContext` into a typed `DraftMessage`. In production the Lead wires
the Outreach agent here; offline and in tests `TemplateDraftProducer` is used. Either way the
result is only a *proposal*: `DraftingService` re-verifies every claim against persisted evidence.
Evidence excerpts come from crawled pages, so they are untrusted data: instruction-like excerpts
are never quoted (ADR-0020).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from kb_contracts.agents import DraftClaim, DraftMessage
from kb_contracts.domain import (
    Domain,
    EvidenceItem,
    OutreachMessage,
    OutreachSequence,
    Prospect,
    ResearchDossier,
)

from kb_core.db.protocols import UnitOfWork
from kb_core.errors import EntityNotFound
from kb_core.research.untrusted import looks_like_instruction, normalise_space


@dataclass(frozen=True)
class DraftContext:
    sequence: OutreachSequence
    prospect: Prospect
    domain: Domain
    dossier: ResearchDossier | None
    evidence: tuple[EvidenceItem, ...]
    step_no: int
    previous_messages: tuple[OutreachMessage, ...]


DraftProducer = Callable[[DraftContext], Awaitable[DraftMessage]]


async def load_draft_context(uow: UnitOfWork, sequence_id: UUID, step_no: int) -> DraftContext:
    seq = await uow.sequences.get(sequence_id)
    if seq is None:
        raise EntityNotFound("outreach_sequence", sequence_id)
    prospect = await uow.prospects.get(seq.prospect_id)
    if prospect is None:
        raise EntityNotFound("prospect", seq.prospect_id)
    domain = await uow.domains.get(prospect.domain_id)
    if domain is None:
        raise EntityNotFound("domain", prospect.domain_id)
    return DraftContext(
        sequence=seq,
        prospect=prospect,
        domain=domain,
        dossier=await uow.dossiers.latest(prospect.id),
        evidence=tuple(await uow.evidence.list_for_prospect(prospect.id)),
        step_no=step_no,
        previous_messages=tuple(await uow.messages.list_for_sequence(seq.id)),
    )


_MAX_EXCERPT = 110
_OPT_OUT = 'If you would rather not hear from us, reply "unsubscribe" and we will not write again.'


def _quotable(item: EvidenceItem) -> str | None:
    text = normalise_space(item.excerpt)
    if len(text) < 12 or looks_like_instruction(text):
        return None
    if len(text) > _MAX_EXCERPT:
        text = text[:_MAX_EXCERPT].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text.replace('"', "'")


def _select(evidence: Sequence[EvidenceItem], limit: int) -> list[tuple[EvidenceItem, str]]:
    picked: list[tuple[EvidenceItem, str]] = []
    seen: set[str] = set()
    for item in evidence:
        text = _quotable(item)
        if text is None or text in seen:
            continue
        seen.add(text)
        picked.append((item, text))
        if len(picked) == limit:
            break
    return picked


class TemplateDraftProducer:
    """Deterministic, evidence-quoting draft. Each claim cites the evidence it quotes."""

    def __init__(self, *, sender_name: str = "Ken Research Outreach Team") -> None:
        self._sender = sender_name

    async def __call__(self, ctx: DraftContext) -> DraftMessage:
        domain = ctx.domain.registered_domain
        quotes = _select(ctx.evidence, 2 if ctx.step_no == 1 else 1)
        claims = tuple(
            DraftClaim(text=f'I noticed your site covers "{text}".', evidence_ids=(item.id,))
            for item, text in quotes
        )
        if ctx.step_no == 1:
            lines = [
                "Hello,",
                "",
                "I am writing from Ken Research, a market research publisher.",
                *[c.text for c in claims],
                f"We would like to propose an original, data-backed guest article for {domain}.",
                "If this suits your editorial calendar, I can share a short outline first.",
                "",
                _OPT_OUT,
                "",
                f"Regards,\n{self._sender}",
            ]
            subject = f"Guest article idea for {domain}"
        else:
            lines = [
                "Hello,",
                "",
                f"Following up in case my earlier note (follow-up {ctx.step_no - 1}) was missed.",
                *[c.text for c in claims],
                "Happy to adapt the topic to what your readers want.",
                "",
                _OPT_OUT,
                "",
                f"Regards,\n{self._sender}",
            ]
            subject = f"Re: Guest article idea for {domain}"
        return DraftMessage(
            sequence_id=ctx.sequence.id,
            step_no=ctx.step_no,
            subject=subject,
            body="\n".join(lines),
            claims=claims,
        )
