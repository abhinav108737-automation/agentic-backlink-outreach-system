"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/reports", tags=["Reports"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("/weekly", operation_id="getWeeklyReport", status_code=501)
async def getWeeklyReport() -> None:
    _not_implemented()
