"""System endpoints (contract C8): health summary and the audited kill switch."""

from fastapi import APIRouter, Request
from kb_contracts import CONTRACT_VERSION
from kb_core.audit.service import OPERATOR

from kb_api.deps import ServicesDep, correlation_id
from kb_api.routes.settings import kill_switch_view
from kb_api.schemas.settings import KillSwitchUpdate, KillSwitchView, SystemHealthView

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/health", operation_id="systemHealth", response_model=SystemHealthView)
async def systemHealth(svc: ServicesDep) -> SystemHealthView:
    state = await svc.system_settings.get_kill_switch()
    return SystemHealthView(
        status="halted" if state.active else "ok",
        contract_version=CONTRACT_VERSION,
        kill_switch_active=state.active,
        real_send_enabled=svc.settings.real_send_enabled,
        autonomy_mode=svc.settings.autonomy_mode,
    )


@router.get("/kill-switch", operation_id="getKillSwitch", response_model=KillSwitchView)
async def getKillSwitch(svc: ServicesDep) -> KillSwitchView:
    return kill_switch_view(await svc.system_settings.get_kill_switch())


@router.put("/kill-switch", operation_id="setKillSwitch", response_model=KillSwitchView)
async def setKillSwitch(
    body: KillSwitchUpdate, request: Request, svc: ServicesDep
) -> KillSwitchView:
    state = await svc.system_settings.set_kill_switch(
        active=body.active,
        reason=body.reason,
        actor=OPERATOR,
        correlation_id=correlation_id(request),
        expected_version=body.expected_version,
    )
    return kill_switch_view(state)
