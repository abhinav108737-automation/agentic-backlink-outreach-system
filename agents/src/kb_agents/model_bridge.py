"""Bridge from the replaceable C5 ``ModelProvider`` to PydanticAI typed outputs.

PydanticAI drives validation and bounded output retries; the provider only ever sees a
``ModelRequest`` (system, prompt, JSON schema). Agents register NO function tools, so the model
cannot select or invoke tools: it returns a typed value or the run fails. Invalid output is a
failure (``UnexpectedModelBehavior`` after the bounded retry), never a coerced result.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from kb_contracts.providers import (
    ModelProvider,
    ModelRequest,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
)
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

DEFAULT_OUTPUT_RETRIES = 1  # one corrective re-ask, then fail


@dataclass
class ModelSession:
    """Per-run model state: who served the call, token counts, the last raw output digest."""

    providers: Sequence[ModelProvider]
    model_name: str | None
    override: Model | None = None
    served_provider: str | None = None
    served_model: str | None = None
    fallback_used: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    last_raw_digest: str | None = None
    timeout_s: float = 120.0
    _pydantic_model: Model | None = field(default=None, repr=False)

    @property
    def provider_name(self) -> str | None:
        if self.served_provider:
            return self.served_provider
        if self.override is not None:
            return f"pydantic-ai:{self.override.system}"
        return self.providers[0].name if self.providers else None

    async def _generate(self, request: ModelRequest) -> Any:
        last: ProviderError | None = None
        for index, provider in enumerate(self.providers):
            try:
                response = await provider.generate(request)
            except (ProviderUnavailable, RateLimited) as exc:
                last = exc
                continue
            self.served_provider = provider.name
            self.fallback_used = index > 0
            self.served_model = response.model
            self.tokens_in += response.tokens_in
            self.tokens_out += response.tokens_out
            return response
        assert last is not None
        raise last

    def _bridge(self) -> FunctionModel:
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            system: list[str] = []
            prompt: list[str] = []
            for message in messages:
                for part in getattr(message, "parts", ()):
                    if isinstance(part, SystemPromptPart):
                        system.append(part.content)
                    elif isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        prompt.append(part.content)
                    elif isinstance(part, RetryPromptPart):
                        prompt.append(
                            "Your previous output was rejected. Fix it and answer again only "
                            f"with valid output for the schema.\n{part.model_response()}"
                        )
            tool = info.output_tools[0] if info.output_tools else None
            request = ModelRequest(
                system="\n\n".join(system),
                prompt="\n\n".join(prompt),
                output_schema=tool.parameters_json_schema if tool is not None else None,
                model=self.model_name,
            )
            response = await self._generate(request)
            raw = response.text
            self.last_raw_digest = (
                f"chars={len(raw)} sha256={hashlib.sha256(raw.encode()).hexdigest()[:16]}"
            )
            if tool is None:
                return ModelResponse(parts=[TextPart(content=raw)])
            args: Any = response.structured if response.structured is not None else raw
            return ModelResponse(parts=[ToolCallPart(tool_name=tool.name, args=args)])

        return FunctionModel(respond, model_name=self.model_name or "provider-bridge")

    async def run_structured[T](self, output_type: type[T], *, system: str, prompt: str) -> T:
        model = self.override or self._bridge()
        agent: Agent[None, T] = Agent(
            model,
            output_type=output_type,
            system_prompt=system,
            retries=DEFAULT_OUTPUT_RETRIES,
        )
        result = await asyncio.wait_for(agent.run(prompt), timeout=self.timeout_s)
        return result.output


class ModelRuntime:
    """Factory of per-run :class:`ModelSession` objects over a provider (plus fallbacks)."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model_name: str | None = None,
        fallbacks: Sequence[ModelProvider] = (),
        pydantic_model: Model | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self._providers = (provider, *fallbacks)
        self._model_name = model_name
        self._override = pydantic_model
        self._timeout_s = timeout_s

    def new_session(self) -> ModelSession:
        return ModelSession(
            providers=self._providers,
            model_name=self._model_name,
            override=self._override,
            timeout_s=self._timeout_s,
        )
