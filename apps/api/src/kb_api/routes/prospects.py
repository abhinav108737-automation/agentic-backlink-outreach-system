"""Prospect endpoints (contract C8). Research/draft stay unimplemented here (other lanes)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from kb_contracts.domain import GuestPostOpportunity
from kb_contracts.enums import OpportunityStatus, ProspectStatus
from kb_core.prospects.views import ProspectDetailView

from kb_api.deps import ServicesDep
from kb_api.schemas.outreach import OpportunityPage
from kb_api.schemas.prospects import ProspectPage

router = APIRouter(prefix="/prospects", tags=["Prospects"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("", operation_id="listProspects", response_model=ProspectPage)
async def listProspects(
    svc: ServicesDep,
    status: ProspectStatus | None = None,
    campaign_id: UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ProspectPage:
    page = await svc.prospects.list(
        campaign_id=campaign_id, status=status, cursor=cursor, limit=limit
    )
    return ProspectPage(items=list(page.items), next_cursor=page.next_cursor)


# Declared before `/{prospect_id}` so the literal segment wins over the UUID path parameter.
@router.get("/opportunities", operation_id="listOpportunities", response_model=OpportunityPage)
async def listOpportunities(
    svc: ServicesDep,
    status: OpportunityStatus | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> OpportunityPage:
    page = await svc.readback.list_opportunities(status=status, cursor=cursor, limit=limit)
    return OpportunityPage(items=list(page.items), next_cursor=page.next_cursor)


@router.get(
    "/opportunities/{opportunity_id}",
    operation_id="getOpportunity",
    response_model=GuestPostOpportunity,
)
async def getOpportunity(opportunity_id: UUID, svc: ServicesDep) -> GuestPostOpportunity:
    return await svc.readback.get_opportunity(opportunity_id)


@router.get("/{prospect_id}", operation_id="getProspect", response_model=ProspectDetailView)
async def getProspect(prospect_id: UUID, svc: ServicesDep) -> ProspectDetailView:
    """Why the prospect is where it is: the qualification decision (with DEFERRED reason and
    `missing_metrics`), each required metric's state, ranking, dossier, evidence, contacts."""
    return await svc.prospects.detail(prospect_id)


@router.post("/{prospect_id}/research", operation_id="researchProspect", status_code=501)
async def researchProspect() -> None:
    _not_implemented()


@router.post("/{prospect_id}/draft-outreach", operation_id="draftOutreach", status_code=501)
async def draftOutreach() -> None:
    _not_implemented()
