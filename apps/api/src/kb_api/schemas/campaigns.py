from typing import Any
from uuid import UUID

from kb_contracts.domain import Campaign
from pydantic import BaseModel, ConfigDict, Field


class CreateCampaignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    goals: dict[str, Any] = Field(default_factory=dict)
    policy_version_id: UUID | None = None


class CampaignActionRequest(BaseModel):
    """Optional operator note recorded as the transition reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class CampaignPage(BaseModel):
    items: list[Campaign]
    next_cursor: str | None = None
