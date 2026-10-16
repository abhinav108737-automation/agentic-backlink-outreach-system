# ADR-0002: PydanticAI is the runtime Agentic Operator

Status: Accepted. Date: 2026-10-16

## Context
The product needs planning, delegation, drafting, reply interpretation and reporting synthesis with typed, validated outputs. Claude Code is the build-time environment, not the runtime. LangGraph was listed as a reference only if a concrete gap remained.

## Decision
PydanticAI is the application-level control plane: one Operator agent plus nine specialist agents, all with Pydantic structured outputs and typed tools. The Operator returns a typed `OperatorDecision` (contract C3) and never mutates protected state. LangGraph is not adopted; durable waits, retries and scheduling are handled by the deterministic job queue (ADR-0008), not by the agent framework.

## Consequences
- Structured-output reliability depends on the local model; the fallback is a deterministic path or human review (ADR-0004).
- Workflow state lives in explicit state machines and DB rows (ADR-0012), not in prompt history.
- CI tests agents with PydanticAI TestModel/FunctionModel, so no model server is needed.
- Adopting LangGraph later would require a new ADR and a documented gap.
