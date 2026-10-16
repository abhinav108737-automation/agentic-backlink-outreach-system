# ADR-0010: Repository structure and monorepo tooling

Status: Accepted. Date: 2026-10-16

## Context
`REPO_STRUCTURE.md` and PRD §23 proposed different layouts (`apps/web-ui` vs `apps/desktop`, `model` vs `models`, `crawling` vs `crawler`, different service sets). Six lanes must work in parallel without editing the same files. The system Python is 3.9, too old for the stack; pnpm is not installed.

## Decision
Adopt the single layout in `REPO_STRUCTURE.md`: `apps/{desktop,api}`, `services/core` (`kb_core`), `services/providers` (`kb_providers`), `agents` (`kb_agents`), `contracts/{python,openapi,generated_ts}`, plus `migrations`, `policies`, `evals`, `fixtures`, `tests`, `infrastructure`, `docs`.

Tooling: a **uv workspace** on Python 3.12 provided by uv (the system 3.9 is never used) and **npm workspaces** (npm 11 is present; pnpm is not a dependency). Quality gates: ruff, mypy strict on `kb_*`, pytest, ESLint, tsc, Vitest, and **import-linter** to enforce boundaries: `kb_agents` must not import `kb_core.db` or the email/crawler/browser providers; `kb_api` must not import `kb_agents` internals; `kb_contracts` imports no other `kb_*` package.

Every directory has exactly one owning lane (docs/10 §2).

## Consequences
- Package names are stable before any code exists, so contracts and imports do not churn.
- Lane collisions are prevented structurally rather than by convention.
- Adding a dependency is a Lead action, since root manifests are Lead-owned.
