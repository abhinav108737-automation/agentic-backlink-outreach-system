# Canonical Repository Structure

Status: canonical (ADR-0010). Owners: Lead = Level-0 integrator; A–F = lanes; QA/Sec = independent reviewers (see `docs/10_IMPLEMENTATION_PLAN.md`).

Tooling: **uv workspace** (Python 3.12, installed via uv) + **npm workspaces**.

```
apps/
  desktop/                     (A)    electron-vite; src/{main,preload,renderer}; tests/
  api/                         (B)    python pkg kb_api: app factory, routes/<area>.py, auth, config, health, middleware
services/
  core/                               python pkg kb_core
    src/kb_core/db/            (C)    SQLAlchemy models (one file per aggregate), session, base repository
    src/kb_core/{campaigns,prospects,qualification,ranking,policy,suppression,idempotency,
                 approvals,settings,scheduling,workflows,audit}/                  (B)
    src/kb_core/{discovery,research,contacts,outreach,replies,negotiation,verification}/   (E)
    src/kb_core/{memory,indexing}/                                                 (C)
    src/kb_core/{analytics,reporting,learning,optimization,experiments}/           (F)
  providers/                          python pkg kb_providers
    src/kb_providers/{search,crawler,browser,seo_metrics,email}/                   (E)
    src/kb_providers/secrets/                                                      (B)
    src/kb_providers/{model,embedding}/                                            (D)
    src/kb_providers/vector/                                                       (C)
    src/kb_providers/{analytics_sources,exporters}/                                (F)
agents/                        (D)    python pkg kb_agents: operator/, specialists/<9>/, tool_gateway/, prompts/, router/
contracts/
  python/                      (Lead, FROZEN)  pkg kb_contracts: DTOs, enums, state machines, provider protocols, events, tool specs
  openapi/openapi.json         (generated snapshot, drift-checked in CI)
  generated_ts/                (generated, never hand-edited)  npm pkg @ken/contracts
  CHANGELOG.md                 (Lead)  contract change requests
migrations/                    (C)    Alembic
policies/                      (B)    seed policy YAML (v1: DA > 45, Spam Score < 1, send limits)
evals/                         (D)    agent eval datasets + runners
fixtures/                      (E; F/QA own their subdirectories)  SERP, HTML sites, metrics CSV, email threads, reply scenarios
tests/
  unit/<area>/, integration/<area>/   owned by the lane that owns <area>
  contract/                    (QA)
  e2e/                         (QA)
  security/                    (Sec)
infrastructure/
  docker/                      (C)    compose.dev.yml
  packaging/                   (A)
  scripts/                     (Lead) check.sh, gen-contracts, drift check, secret scan
docs/
  00–14 numbered specs, adr/, phase-reports/, runbooks/, research/ (repo provenance),
  reference/{source,methodology}/   source PRDs and methodology-only material (excluded from packaging)
reference/architecture_reference.png  canonical backlink-outreach architecture diagram
.claude/{agents,rules,skills}/        Claude Code subagents, path rules, phase-gate/release-check/architecture-review skills
```

## Import boundaries (enforced with import-linter)
- `kb_agents` must not import `kb_core.db` or the email/crawler/browser providers; it acts only through ToolGateway.
- `kb_api` must not import `kb_agents` internals; it invokes workflows/services.
- `kb_contracts` imports nothing from other `kb_*` packages.
- Nothing imports from `contracts/generated_ts` except the desktop app.

## Packaging exclusions
`docs/`, `reference/`, `fixtures/`, `evals/`, `tests/` are never shipped in the Electron package.
