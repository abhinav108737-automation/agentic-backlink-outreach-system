"""Real ToolGateway capabilities, backed by deterministic services and mock providers.

Every capability is an ``async (ToolContext, validated_args) -> Mapping`` registered by tool
name. What agents get is exactly this set and nothing else: no session, no connection, no
engine, no provider handle, no secret, no shell and no email send. Side effects go through
repositories and deterministic services, so policy, audit and the state machines still apply.

Three SEO metric states are preserved end to end and must not be collapsed (ADR-0015):
``metrics[m]`` is a finite float (fetched with a value), ``None`` (fetched but unavailable), or
the key is **absent** (never fetched). The last two both mean the metric is missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from kb_agents.tools.gateway import Capability, ToolContext
from kb_contracts.enums import ActorType, ProspectStatus
from kb_contracts.events import Actor
from kb_contracts.providers import CrawlerProvider, SearchProvider, SEOMetricsProvider
from kb_core.db.protocols import UnitOfWork, UnitOfWorkFactory
from kb_core.discovery import InvalidUrl, canonicalize_url, registered_domain
from kb_core.errors import EntityNotFound, InvalidInput
from kb_core.transitions.service import TransitionService

AGENT_ACTOR = Actor(actor_type=ActorType.AGENT, actor_id="agent")

# fetch_page is the only outbound-looking capability. Agents never get unrestricted HTTP: the
# scheme is allowlisted here and the provider itself is a fixture-backed mock at I1.
_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class CapabilityDeps:
    uow_factory: UnitOfWorkFactory
    search: SearchProvider
    crawler: CrawlerProvider
    seo_metrics: SEOMetricsProvider
    transitions: TransitionService


def _actor(ctx: ToolContext) -> Actor:
    return Actor(actor_type=ActorType.AGENT, actor_id=f"agent:{ctx.agent.value}")


def _cid(ctx: ToolContext) -> str:
    return ctx.correlation_id or "-"


async def _require_prospect(uow: UnitOfWork, prospect_id: UUID) -> Any:
    prospect = await uow.prospects.get(prospect_id)
    if prospect is None:
        raise EntityNotFound("prospect", prospect_id)
    return prospect


def build_capabilities(deps: CapabilityDeps) -> dict[str, Capability]:  # noqa: C901
    """The approved capability set. A tool absent from this mapping is simply unavailable:
    the gateway answers ``capability_not_wired`` rather than improvising."""

    # ---------------------------------------------------------------------------- reads

    async def read_campaign_state(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        async with deps.uow_factory() as uow:
            campaign = await uow.campaigns.get(args.campaign_id)
            if campaign is None:
                raise EntityNotFound("campaign", args.campaign_id)
            targets = await uow.campaigns.list_targets(args.campaign_id)
        topics = tuple(dict.fromkeys(t.topic for t in targets if t.topic))
        queries = tuple(dict.fromkeys(k for t in targets for k in t.keywords))
        return {
            "campaign_id": campaign.id,
            "name": campaign.name,
            "topics": topics,
            "seed_queries": queries,
            "status": campaign.status.value,
        }

    async def read_policy(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        async with deps.uow_factory() as uow:
            version = (
                await uow.policies.active_version_for_campaign(args.campaign_id)
                if args.campaign_id is not None
                else await uow.policies.active_global_version()
            )
            if version is None:
                raise EntityNotFound("policy_version", args.campaign_id or UUID(int=0))
            topics: tuple[str, ...] = ()
            if args.campaign_id is not None:
                targets = await uow.campaigns.list_targets(args.campaign_id)
                topics = tuple(dict.fromkeys(t.topic for t in targets if t.topic))
        return {
            "policy_version": str(version.id),
            "required_metrics": tuple(version.body.required_metrics),
            "campaign_topics": topics,
        }

    async def read_memory(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        # MemoryService is not built yet (Wave 2). Returning no snippets is honest: an agent
        # must behave correctly with an empty memory, and a stub that invented snippets would
        # make the research path look better than it is.
        return {"snippets": ()}

    async def search(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        results = await deps.search.search(args.query, limit=args.limit)
        return {"provider": deps.search.name, "results": tuple(results)}

    async def fetch_page(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        try:
            canonical = canonicalize_url(args.url)
        except InvalidUrl as exc:
            raise InvalidInput(f"unfetchable url: {exc}") from exc
        # canonicalize_url already rejects opaque and non-web schemes; this re-checks the
        # result rather than trusting it, so a future canonicalizer change cannot silently
        # widen what an agent may fetch.
        if not any(canonical.canonical.startswith(f"{s}://") for s in _ALLOWED_SCHEMES):
            raise InvalidInput(f"not a fetchable url: {canonical.canonical!r}")
        page = await deps.crawler.fetch(canonical.canonical)
        return {"page": page}

    async def read_domain_metrics(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        """Fetch through the provider, persist every reading (including unavailable ones as
        NULL), then read back through ``latest()``, which is the authoritative three-state
        reader. Persisting first means the metric snapshot is auditable and the DEFER decision
        is reproducible from the database alone."""
        domain = args.domain.strip().lower()
        wanted: Sequence[str] = tuple(args.metrics)
        readings = await deps.seo_metrics.get_metrics(domain, wanted)
        async with deps.uow_factory() as uow:
            row = await uow.domains.get_or_create(domain)
            for reading in readings:
                # A metric the provider did not return at all is never fetched: no row is
                # written for it, so `latest()` leaves the key absent. A reading with
                # value=None is written as NULL: fetched, unavailable.
                await uow.domain_metrics.record(
                    domain_id=row.id,
                    provider=deps.seo_metrics.name,
                    metric=reading.metric,
                    value=reading.value,
                    source=reading.source,
                    fetched_at=reading.fetched_at,
                )
            latest = await uow.domain_metrics.latest(row.id, wanted)
        return {"domain": domain, "metrics": latest}

    async def read_evidence(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        async with deps.uow_factory() as uow:
            items = await uow.evidence.list_for_prospect(args.prospect_id)
        return {
            "items": tuple(
                {
                    "evidence_id": item.id,
                    "source_url": item.source_url,
                    "excerpt": item.excerpt,
                    "content_hash": item.content_hash,
                    "kind": item.kind,
                }
                for item in items[: args.limit]
            )
        }

    # --------------------------------------------------------------------------- records

    async def record_candidates(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        """Persist discovered candidates. Idempotent by construction: one prospect per
        (campaign, registered domain) via the DB unique constraint, and one prospect_url per
        (prospect, url_hash). Re-running discovery cannot create duplicates."""
        first_created: UUID | None = None
        async with deps.uow_factory() as uow:
            for candidate in args.result.candidates:
                host = registered_domain(candidate.domain.strip().lower())
                domain = await uow.domains.get_or_create(host)
                prospect = await uow.prospects.get_by_domain(args.campaign_id, domain.id)
                if prospect is None:
                    prospect = await uow.prospects.create(
                        campaign_id=args.campaign_id,
                        domain_id=domain.id,
                        discovered_via=candidate.provenance.source,
                    )
                    first_created = first_created or prospect.id
                    await uow.audit.append(
                        actor_type=_actor(ctx).actor_type.value,
                        actor_id=_actor(ctx).actor_id,
                        action="prospect.discovered",
                        subject_type="prospect",
                        subject_id=prospect.id,
                        correlation_id=_cid(ctx),
                        new_state={"domain": host, "source": candidate.provenance.source},
                        reason="discovery agent candidate",
                        agent_run_id=ctx.agent_run_id,
                    )
                for raw in candidate.urls:
                    try:
                        canonical = canonicalize_url(raw)
                    except InvalidUrl:
                        continue
                    await uow.prospect_urls.upsert(
                        prospect_id=prospect.id,
                        url=raw,
                        canonical_url=canonical.canonical,
                        url_hash=canonical.url_hash,
                        provenance={
                            "source": candidate.provenance.source,
                            "query": candidate.provenance.query,
                            "url": candidate.provenance.url,
                            "agent_run_id": str(ctx.agent_run_id),
                        },
                    )
        # RecordAck forbids extra keys: the gateway validates capability output, so the shape
        # declared in kb_agents/tools/specs.py is the shape that must be returned.
        return {"record_id": first_created}

    async def record_assessment(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        """The agent's assessment is ADVISORY (frozen `QualificationAssessment` has no outcome
        field). It is audited for explainability; the ELIGIBLE/DEFERRED/REJECTED decision stays
        with the deterministic PolicyEngine."""
        assessment = args.assessment
        async with deps.uow_factory() as uow:
            await _require_prospect(uow, assessment.prospect_id)
            event_id = await uow.audit.append(
                actor_type=_actor(ctx).actor_type.value,
                actor_id=_actor(ctx).actor_id,
                action="prospect.assessed",
                subject_type="prospect",
                subject_id=assessment.prospect_id,
                correlation_id=_cid(ctx),
                new_state={
                    "relevance": assessment.relevance,
                    "editorial_fit": assessment.editorial_fit,
                    "notes": assessment.notes,
                    "advisory": True,
                },
                reason="qualification agent assessment (advisory)",
                agent_run_id=ctx.agent_run_id,
            )
        return {"record_id": event_id}

    async def record_evidence(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        async with deps.uow_factory() as uow:
            await _require_prospect(uow, args.prospect_id)
            existing = {
                e.content_hash: e.id for e in await uow.evidence.list_for_prospect(args.prospect_id)
            }
            fresh = [i for i in args.items if i.content_hash not in existing]
            stored = await uow.evidence.record_many(
                args.prospect_id,
                [
                    {
                        "source_url": i.source_url,
                        "excerpt": i.excerpt,
                        "content_hash": i.content_hash,
                        "kind": i.kind,
                        "extracted_at": datetime.now(UTC),
                        "extractor": "deterministic",
                    }
                    for i in fresh
                ],
            )
        refs = dict(existing)
        refs.update({item.content_hash: item.id for item in stored})
        return {
            "refs": tuple(
                {"content_hash": i.content_hash, "evidence_id": refs[i.content_hash]}
                for i in args.items
                if i.content_hash in refs
            ),
            "duplicate": not fresh and bool(args.items),
        }

    async def record_dossier(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        draft = args.dossier
        async with deps.uow_factory() as uow:
            prospect = await _require_prospect(uow, draft.prospect_id)
            dossier = await uow.dossiers.create(
                prospect_id=draft.prospect_id,
                summary=draft.summary,
                topics=tuple(draft.topics),
                editorial_signals={"unsupported_claims": list(draft.unsupported_claims)},
                agent_run_id=ctx.agent_run_id,
            )
            if prospect.status is ProspectStatus.RANKED:
                await deps.transitions.transition_in(
                    uow,
                    prospect,
                    ProspectStatus.RESEARCHED,
                    actor=_actor(ctx),
                    reason="research dossier stored",
                    correlation_id=_cid(ctx),
                )
        return {"record_id": dossier.id}

    async def record_contact_candidates(ctx: ToolContext, args: Any) -> Mapping[str, Any]:
        candidates = args.candidates
        async with deps.uow_factory() as uow:
            prospect = await _require_prospect(uow, candidates.prospect_id)
            contact = None
            for candidate in candidates.candidates:
                if contact is None:
                    contact = await uow.contacts.create(
                        domain_id=prospect.domain_id,
                        name=candidate.name,
                        role=candidate.role,
                        contact_type="generic",
                        confidence=candidate.confidence,
                        provenance={"agent_run_id": str(ctx.agent_run_id)},
                    )
                await uow.contacts.add_channel(
                    contact_id=contact.id,
                    channel_type=candidate.channel_type,
                    value_normalized=candidate.value.strip().lower(),
                    confidence=candidate.confidence,
                    evidence_item_id=(
                        candidate.evidence_ids[0] if candidate.evidence_ids else None
                    ),
                )
            if contact is not None and prospect.status is ProspectStatus.RESEARCHED:
                await deps.transitions.transition_in(
                    uow,
                    prospect,
                    ProspectStatus.CONTACT_READY,
                    actor=_actor(ctx),
                    reason="contact channel found",
                    correlation_id=_cid(ctx),
                )
        return {"record_id": contact.id if contact is not None else None}

    return {
        "read_campaign_state": read_campaign_state,
        "read_policy": read_policy,
        "read_memory": read_memory,
        "search": search,
        "fetch_page": fetch_page,
        "read_domain_metrics": read_domain_metrics,
        "read_evidence": read_evidence,
        "record_candidates": record_candidates,
        "record_assessment": record_assessment,
        "record_evidence": record_evidence,
        "record_dossier": record_dossier,
        "record_contact_candidates": record_contact_candidates,
    }


__all__ = ["AGENT_ACTOR", "CapabilityDeps", "build_capabilities"]
