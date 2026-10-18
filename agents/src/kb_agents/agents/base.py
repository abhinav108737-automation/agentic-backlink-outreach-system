"""Shared helpers for specialist agents."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from kb_agents.runs.lifecycle import ToolCallFailed
from kb_agents.tools.gateway import Toolbox
from kb_agents.tools.specs import ToolModel


class TaskModel(BaseModel):
    """Agent task input: strict and immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelOutput(BaseModel):
    """Internal model-facing output schema: strict, no coercion of unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


async def call_typed[R: ToolModel](
    toolbox: Toolbox, tool: str, args: ToolModel, result_type: type[R]
) -> R:
    """Call a tool via the gateway; a refused/failed call raises ``ToolCallFailed``."""
    result = await toolbox.call(tool, args)
    if not result.ok:
        raise ToolCallFailed(tool, result.error_code)
    return result_type.model_validate(result.data)


async def call_ack(toolbox: Toolbox, tool: str, args: ToolModel) -> None:
    result = await toolbox.call(tool, args)
    if not result.ok:
        raise ToolCallFailed(tool, result.error_code)
