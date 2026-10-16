# ADR-0016: Contract-first parallel lanes and contract freeze

Status: Accepted. Date: 2026-10-16

## Context
Two sequential timelines existed (8 phases over weeks; 10 phases over 25–36 engineer-weeks). Neither suits agent-team execution. CLAUDE.md requires parallel workstreams that never edit the same files concurrently, with the Level-0 lead owning integration.

## Decision
Neither old timeline is used. Execution is contract-first and parallel: a short Foundation Gate, then **freeze shared contracts v0.1** (C1–C13, docs/14), then six lanes in separate worktrees — A Electron/UI, B FastAPI/deterministic backend, C PostgreSQL/Qdrant/data, D PydanticAI Operator/agents, E discovery/crawling/outreach/providers, F reporting/ROI/learning/optimization. QA and Security review independently and own only their test trees.

Targets: Foundation Gate ~12 h, MVP ~72 h, integrated V1 ~5–8 working days, hardened RC ~10–14 calendar days. Integration happens only at checkpoints I1 (H36), I2 (H60) and I3 (H72).

Collision rules: one owning lane per directory; ORM models and migrations are Lane C only; route modules are pre-registered as stubs at the freeze so the router registry is never edited again; dependency additions go through the Lead. Contract changes require a Contract Change Request to the Lead, a `contracts/CHANGELOG.md` entry, a version bump and TS regeneration; lanes adopt them at the next checkpoint.

## Consequences
- Lanes work against fakes before their dependencies land, so idle time is minimal.
- A wrong contract is expensive, which justifies the Foundation Gate's thoroughness.
- If the 72-hour target slips, the cut order is UI polish, then shadow/canary depth; policy, suppression, idempotency and audit tests are never cut.
