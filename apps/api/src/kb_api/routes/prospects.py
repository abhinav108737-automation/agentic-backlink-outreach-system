"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/prospects", tags=["Prospects"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("", operation_id="listProspects", status_code=501)
async def listProspects() -> None:
    _not_implemented()


@router.get("/{prospect_id}", operation_id="getProspect", status_code=501)
async def getProspect() -> None:
    _not_implemented()


@router.post("/{prospect_id}/research", operation_id="researchProspect", status_code=501)
async def researchProspect() -> None:
    _not_implemented()


@router.post("/{prospect_id}/draft-outreach", operation_id="draftOutreach", status_code=501)
async def draftOutreach() -> None:
    _not_implemented()
