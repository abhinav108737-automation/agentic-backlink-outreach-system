"""Thread readback (contract C8)."""

from uuid import UUID

from fastapi import APIRouter
from kb_core.readback.views import ThreadView

from kb_api.deps import ServicesDep

router = APIRouter(prefix="/threads", tags=["Outreach"])


@router.get("/{thread_id}", operation_id="getThread", response_model=ThreadView)
async def getThread(thread_id: UUID, svc: ServicesDep) -> ThreadView:
    """The thread with its messages and replies."""
    return await svc.readback.get_thread(thread_id)
