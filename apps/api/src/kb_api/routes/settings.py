"""Settings endpoints (contract C8). Only the kill switch is mutable at runtime."""

from fastapi import APIRouter, Request
from kb_core.audit.service import OPERATOR
from kb_core.settings.service import KillSwitchState

from kb_api.deps import ServicesDep, correlation_id
from kb_api.schemas.settings import KillSwitchView, SettingsView, UpdateSettingsRequest

router = APIRouter(prefix="/settings", tags=["Settings"])


def kill_switch_view(state: KillSwitchState) -> KillSwitchView:
    return KillSwitchView(
        active=state.active,
        reason=state.reason,
        changed_by=state.changed_by,
        version=state.version,
    )


async def settings_view(svc: ServicesDep) -> SettingsView:
    s = svc.settings
    return SettingsView(
        kill_switch=kill_switch_view(await svc.system_settings.get_kill_switch()),
        real_send_enabled=s.real_send_enabled,
        autonomy_mode=s.autonomy_mode,
        default_daily_send_limit=s.default_daily_send_limit,
        email_provider=s.email_provider,
        seo_metrics_provider=s.seo_metrics_provider,
        search_provider=s.search_provider,
        crawler_provider=s.crawler_provider,
    )


@router.get("", operation_id="getSettings", response_model=SettingsView)
async def getSettings(svc: ServicesDep) -> SettingsView:
    return await settings_view(svc)


@router.put("", operation_id="updateSettings", response_model=SettingsView)
async def updateSettings(
    body: UpdateSettingsRequest, request: Request, svc: ServicesDep
) -> SettingsView:
    if body.kill_switch is not None:
        await svc.system_settings.set_kill_switch(
            active=body.kill_switch.active,
            reason=body.kill_switch.reason,
            actor=OPERATOR,
            correlation_id=correlation_id(request),
            expected_version=body.kill_switch.expected_version,
        )
    return await settings_view(svc)
