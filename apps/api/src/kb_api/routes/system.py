"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/system", tags=["System"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/health", operation_id="systemHealth", status_code=501)
async def systemHealth() -> None:
    _not_implemented()
