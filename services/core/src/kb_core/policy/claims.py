"""Claim-to-evidence verification shared by the policy gate (UNSUPPORTED_CLAIM) and the
DRAFT -> QA_PASSED transition guard. Deterministic: a claim passes only if every evidence id it
cites resolves to a persisted evidence item belonging to the message's own prospect."""

from __future__ import annotations

from uuid import UUID

from kb_contracts.domain import OutreachMessage

from kb_core.db.protocols import UnitOfWork


async def unsupported_claims(
    uow: UnitOfWork, message: OutreachMessage, prospect_id: UUID
) -> tuple[int, ...]:
    """Indices (in claim order) of claims that lack real evidence. A claim citing no evidence,
    an unknown evidence id, or evidence of another prospect is unsupported. Claim text is not
    returned: it is agent-authored content and does not belong in audit detail."""
    claims = list(message.claims.items())
    wanted = list(dict.fromkeys(e for _, ids in claims for e in ids))
    found = {e.id: e for e in await uow.evidence.get_many(wanted)} if wanted else {}
    bad: list[int] = []
    for i, (_, ids) in enumerate(claims):
        if not ids or any(e not in found or found[e].prospect_id != prospect_id for e in ids):
            bad.append(i)
    return tuple(bad)
