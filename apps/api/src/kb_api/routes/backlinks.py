"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/backlinks", tags=["Backlinks"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/{backlink_id}/verify", operation_id="verifyBacklink", status_code=501)
async def verifyBacklink() -> None:
    _not_implemented()
