"""Campaign endpoints (contract C8). Thin: parsing and mapping only; logic is in kb_core."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from kb_contracts.domain import Campaign, OutreachSequence
from kb_contracts.enums import SequenceStatus
from kb_core.audit.service import OPERATOR
from kb_core.errors import EntityNotFound

from kb_api.deps import ServicesDep, correlation_id
from kb_api.schemas.campaigns import CampaignActionRequest, CampaignPage, CreateCampaignRequest
from kb_api.schemas.outreach import SequencePage

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("", operation_id="createCampaign", status_code=201, response_model=Campaign)
async def createCampaign(
    body: CreateCampaignRequest,
    request: Request,
    response: Response,
    svc: ServicesDep,
    idempotency_key: IdempotencyKey = None,
) -> Campaign:
    campaign, replayed = await svc.campaigns.create(
        name=body.name,
        goals=body.goals,
        policy_version_id=body.policy_version_id,
        actor=OPERATOR,
        correlation_id=correlation_id(request),
        idempotency_key=idempotency_key,
    )
    if replayed:
        response.headers["Idempotency-Replayed"] = "true"
    return campaign


@router.get("", operation_id="listCampaigns", response_model=CampaignPage)
async def listCampaigns(
    svc: ServicesDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CampaignPage:
    page = await svc.campaigns.list(cursor=cursor, limit=limit)
    return CampaignPage(items=list(page.items), next_cursor=page.next_cursor)


@router.get("/{campaign_id}", operation_id="getCampaign", response_model=Campaign)
async def getCampaign(campaign_id: UUID, svc: ServicesDep) -> Campaign:
    return await svc.campaigns.get(campaign_id)


@router.post("/{campaign_id}/activate", operation_id="activateCampaign", response_model=Campaign)
async def activateCampaign(
    campaign_id: UUID,
    request: Request,
    svc: ServicesDep,
    body: CampaignActionRequest | None = None,
) -> Campaign:
    return await svc.campaigns.activate(
        campaign_id,
        actor=OPERATOR,
        reason=body.reason if body else None,
        correlation_id=correlation_id(request),
    )


@router.post("/{campaign_id}/run", operation_id="runCampaign", response_model=Campaign)
async def runCampaign(
    campaign_id: UUID,
    request: Request,
    svc: ServicesDep,
    body: CampaignActionRequest | None = None,
) -> Campaign:
    """Starts the campaign: the same guarded DRAFT/PAUSED -> ACTIVE transition as `activate`.
    It enqueues no work and sends nothing; automation stays behind the policy gate."""
    return await svc.campaigns.activate(
        campaign_id,
        actor=OPERATOR,
        reason=body.reason if body else "campaign run requested",
        correlation_id=correlation_id(request),
    )


@router.post("/{campaign_id}/pause", operation_id="pauseCampaign", response_model=Campaign)
async def pauseCampaign(
    campaign_id: UUID,
    request: Request,
    svc: ServicesDep,
    body: CampaignActionRequest | None = None,
) -> Campaign:
    return await svc.campaigns.pause(
        campaign_id,
        actor=OPERATOR,
        reason=body.reason if body else None,
        correlation_id=correlation_id(request),
    )


@router.get(
    "/{campaign_id}/sequences", operation_id="listCampaignSequences", response_model=SequencePage
)
async def listCampaignSequences(
    campaign_id: UUID,
    svc: ServicesDep,
    status: SequenceStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SequencePage:
    await svc.campaigns.get(campaign_id)  # 404 for an unknown campaign
    page = await svc.readback.list_sequences(
        campaign_id=campaign_id, status=status, cursor=cursor, limit=limit
    )
    return SequencePage(items=list(page.items), next_cursor=page.next_cursor)


@router.get(
    "/{campaign_id}/sequences/{sequence_id}",
    operation_id="getCampaignSequence",
    response_model=OutreachSequence,
)
async def getCampaignSequence(
    campaign_id: UUID, sequence_id: UUID, svc: ServicesDep
) -> OutreachSequence:
    sequence = await svc.readback.get_sequence(sequence_id)
    if sequence.campaign_id != campaign_id:
        raise EntityNotFound("outreach_sequence", sequence_id)
    return sequence
