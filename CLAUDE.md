# CLAUDE.md — Ken Research Agentic AI Backlink Outreach System

## Mission
Build a production-quality standalone Electron application that autonomously discovers, qualifies, researches, outreaches, follows up, interprets replies, supports negotiation, verifies backlinks, reports weekly performance, learns from outcomes, and proposes governed optimizations.

## Canonical architecture
Electron Desktop App → PydanticAI Agentic Operator → Specialist Agents → Deterministic Services → PostgreSQL + Memory/Vector Layer → Learning & Optimization Layer → Optimization Feedback → PydanticAI Operator.

## Non-negotiable decisions
- Electron + React + TypeScript desktop UI.
- Python + FastAPI backend.
- PydanticAI for the application operator/control plane.
- PostgreSQL is authoritative.
- Qdrant is derived/rebuildable semantic state.
- Ollama is the default runtime model provider.
- Zero mandatory paid per-token AI APIs.
- Provider interfaces must be replaceable.
- Real outbound email is OFF by default.
- Hard eligibility, suppression, rate limits, idempotency, audit, version checks, and verification are deterministic.
- Agents never receive unrestricted production DB/email/browser mutation access.
- Every consequential action is typed, auditable, attributable, retry-bounded, and reversible where possible.
- Learning never directly promotes policy/skill changes without evaluation gates.

## Initial backlink policy
- DA > 45.
- Spam Score < 1.
- Configurable by campaign.
- Exact proprietary Moz metrics are a provider concern and may remain TBD if no zero-cost legal source is available.

## Development principles
- Capability first; provider second.
- Deterministic before agentic.
- Tests before risky integrations.
- No production credentials in repo, logs, fixtures, screenshots, prompts, or commits.
- Never silently use paid services.
- Mark unknowns as TBD rather than fabricating.
- Use migrations for schema changes.
- Use typed contracts between frontend and backend.
- Keep modules replaceable.
- Prefer explicit state machines over prompt-only workflow state.
- Update ADRs for major architecture changes.

## Quality gates
Every phase must pass lint, type checks, relevant tests, migration checks, contract checks, security review, architecture review, documentation update, and a git checkpoint.

## Work strategy
- Plan before modifying more than one architectural area.
- Parallelize independent work with agent teams/worktrees.
- Use isolated subagents for research, testing, security, data modelling, and code review.
- Main agent owns integration and final decisions.
- Do not let two agents modify the same files concurrently.
- One workstream = one branch/worktree where possible.

## Required references
Read docs/00_PRODUCT_BRIEF.md through docs/12_REPOSITORY_RESEARCH.md plus TASK_BOARD.md.

## Definition of done
A local end-to-end flow runs from discovery through qualification, research, mock outreach, reply simulation, backlink verification simulation, reporting, learning input, and optimization proposal with complete audit history and passing tests.
