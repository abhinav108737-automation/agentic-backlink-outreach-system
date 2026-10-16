"""Route stubs (contract C8). Handlers are implemented by the owning lane."""

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/messages", tags=["Outreach"])


def _not_implemented() -> None:
    raise HTTPException(status_code=501, detail="Not implemented")


@router.post("/{message_id}/approve", operation_id="approveMessage", status_code=501)
async def approveMessage() -> None:
    _not_implemented()


@router.post("/{message_id}/send", operation_id="sendMessage", status_code=501)
async def sendMessage() -> None:
    _not_implemented()
