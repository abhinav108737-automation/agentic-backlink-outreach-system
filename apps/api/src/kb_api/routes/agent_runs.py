"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/agent-runs", tags=["Agent Runs"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("", operation_id="listAgentRuns", status_code=501)
async def listAgentRuns() -> None:
    _not_implemented()
