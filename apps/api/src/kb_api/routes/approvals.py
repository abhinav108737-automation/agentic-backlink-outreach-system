"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/approvals", tags=["Approvals"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.get("", operation_id="listApprovals", status_code=501)
async def listApprovals() -> None:
    _not_implemented()


@router.post("/{approval_id}/decide", operation_id="decideApproval", status_code=501)
async def decideApproval() -> None:
    _not_implemented()
