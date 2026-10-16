"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("", operation_id="createCampaign", status_code=501)
async def createCampaign() -> None:
    _not_implemented()


@router.get("/{campaign_id}", operation_id="getCampaign", status_code=501)
async def getCampaign() -> None:
    _not_implemented()


@router.post("/{campaign_id}/run", operation_id="runCampaign", status_code=501)
async def runCampaign() -> None:
    _not_implemented()


@router.post("/{campaign_id}/pause", operation_id="pauseCampaign", status_code=501)
async def pauseCampaign() -> None:
    _not_implemented()
