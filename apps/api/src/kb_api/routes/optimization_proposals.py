"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/optimization-proposals", tags=["Optimization"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/{proposal_id}/promote", operation_id="promoteProposal", status_code=501)
async def promoteProposal() -> None:
    _not_implemented()
