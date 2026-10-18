from kb_contracts.enums import AutonomyMode
from pydantic import BaseModel, ConfigDict, Field


class KillSwitchView(BaseModel):
    active: bool
    reason: str | None = None
    changed_by: str | None = None
    version: int  # pass back as expected_version for compare-and-set; 0 = never written


class KillSwitchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
    reason: str | None = Field(default=None, max_length=500)
    expected_version: int | None = Field(default=None, ge=0)


class SettingsView(BaseModel):
    kill_switch: KillSwitchView
    real_send_enabled: bool
    autonomy_mode: AutonomyMode
    default_daily_send_limit: int
    email_provider: str
    seo_metrics_provider: str
    search_provider: str
    crawler_provider: str


class UpdateSettingsRequest(BaseModel):
    """Only the kill switch is mutable at runtime. `real_send_enabled` and `autonomy_mode` are
    deliberately absent (extra fields are rejected): enabling real send needs an approved
    live_send_activation (ADR-0007) and is not available in this build."""

    model_config = ConfigDict(extra="forbid")

    kill_switch: KillSwitchUpdate | None = None


class SystemHealthView(BaseModel):
    status: str  # ok | halted
    contract_version: str
    kill_switch_active: bool
    real_send_enabled: bool
    autonomy_mode: AutonomyMode
