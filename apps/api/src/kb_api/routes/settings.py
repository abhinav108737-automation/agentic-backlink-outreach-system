"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/settings", tags=["Settings"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("", operation_id="getSettings", status_code=501)
async def getSettings() -> None:
    _not_implemented()


@router.put("", operation_id="updateSettings", status_code=501)
async def updateSettings() -> None:
    _not_implemented()
