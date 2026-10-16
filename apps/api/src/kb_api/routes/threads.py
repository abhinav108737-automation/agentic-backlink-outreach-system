"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/threads", tags=["Outreach"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/{thread_id}", operation_id="getThread", status_code=501)
async def getThread() -> None:
    _not_implemented()
