# Implementation Plan — Contract-First Parallel Delivery

Status: canonical (ADR-0016). This replaces both earlier sequential timelines (docs v0 and PRD §25), which are **not** used.

## Targets
| Gate | Target | Definition |
|---|---|---|
| Foundation Gate | ~12 h after baseline approval | §5 checklist green; contracts v0.1 frozen |
| MVP DONE | ~72 h | fixture/mock-driven local end-to-end loop with complete audit history and passing tests (docs/11) |
| V1 DONE | ~5–8 working days | real approved providers (off by default), local-model-only path, packaging, backup/restore, provenance, hardening, runbooks |
| Hardened release candidate | ~10–14 calendar days | security hardening, soak, Windows packaging if users are on Windows |

72 hours is aggressive. If it slips, cut UI polish first, then shadow/canary depth. Policy, suppression, idempotency and audit tests are never cut.

## 1. Execution model
1. **Short Foundation Gate** (Lead): scaffold, contracts v0.1, migration 001, auth handshake, Electron shell, CI.
2. **Freeze shared contracts** (docs/14).
3. **Launch six parallel lanes**, each in its own git worktree on branch `lane/<x>-<name>`. The Lead integrates only at checkpoints I1/I2/I3 and phase gates. QA and Security review independently.

## 2. Lane ownership matrix
| Lane | Subagent | Owns (exclusive write) | Consumes |
|---|---|---|---|
| Lead (Level 0) | main session | `contracts/**`, root `pyproject.toml` / `package.json` / lockfiles, CI workflow, `.gitignore`, `infrastructure/scripts/**`, `docs/adr/**`, `docs/01` registry, CLAUDE.md, merges | all |
| A — Electron/React/UI | desktop-engineer | `apps/desktop/**`, `infrastructure/packaging/**` | C8–C10 |
| B — FastAPI/deterministic backend | backend-engineer | `apps/api/**`; kb_core `campaigns, prospects, qualification, ranking, policy, suppression, idempotency, approvals, settings, scheduling, workflows, audit`; `policies/**`; providers `secrets` | C1, C2, C6–C8, C11 |
| C — PostgreSQL/Qdrant/data | data-engineer | kb_core `db/**`, `memory`, `indexing`; `migrations/**`; providers `vector`; `infrastructure/docker/**` | C1, C2, C11 |
| D — PydanticAI Operator/agents | agentic-engineer | `agents/**`; providers `model`, `embedding`; `evals/**` | C3–C6 |
| E — Discovery/crawling/outreach/providers | outreach-engineer | kb_core `discovery, research, contacts, outreach, replies, negotiation, verification`; providers `search, crawler, browser, seo_metrics, email`; `fixtures/**` | C2, C5, C6, C12 |
| F — Reporting/ROI/learning/optimization | learning-reporting-engineer | kb_core `analytics, reporting, learning, optimization, experiments`; providers `analytics_sources, exporters` | C1–C3, C7 |
| QA (independent) | qa-engineer | `tests/e2e/**`, `tests/contract/**` | all |
| Security (independent) | security-reviewer | `tests/security/**`, review reports in `docs/phase-reports/` | all |

Each lane owns the `tests/unit/<area>` and `tests/integration/<area>` subtrees matching its source areas.

Rules that keep lanes from colliding:
- No two workstreams modify the same file concurrently.
- ORM models and migrations are Lane C only. Other lanes request schema changes from C.
- All route modules `apps/api/src/kb_api/routes/<area>.py` are pre-registered as stubs at the freeze, so the router registry is never edited afterwards. Each area lane owns its route module's handlers.
- Dependency additions go through the Lead (root manifests are Lead-only).
- Until a real dependency lands, lanes code against contract fakes and in-memory repositories.

## 3. Dependency graph
```
G0.1 hygiene + config extraction
  └─► G0.2 canonical docs, registry, data model, state machines, ADRs   (done before Foundation scaffold)
        └─► G0.3 monorepo scaffold (uv + npm workspaces, compose, lint/type/test config)
              ├─► G0.4 contracts v0.1 (C1–C13) ─► G0.5 migration 001 + audit trigger
              └─► G0.6 FastAPI skeleton + auth handshake + health ─► G0.7 Electron shell + bridge
                                                                   └─► G0.8 OpenAPI → TS + drift check
        G0.4 … G0.8 ─► G0.9 CI gate green ─► CONTRACT FREEZE ─► lanes A–F in parallel

Within lanes:
  C repositories / audit trigger / Qdrant      ─► B, E, F persistence
  B policy gate + job queue + transitions      ─► E send pipeline + follow-ups; D ToolGateway
  E mock providers + fixture corpus (day 1)    ─► D agent evals; F analytics inputs; QA E2E
  D agents                                     ─► E (drafts, classification); F (narrative)
  F snapshots                                  ─► A Reports UI
  Spikes: DBOS (B, 4 h) ─► ADR-0008 final;  Ollama model eval (D, 4 h) ─► ADR-0004 addendum

Integration checkpoints:
  I1 (H36) discover → qualify (incl. DEFERRED) → research → contact, via API and UI
  I2 (H60) draft → mock send → reply → negotiate → publish → verify
  I3 (H72) weekly report → learning input → optimization proposal; full-loop E2E green
```

## 4. First 72-hour schedule (hours after baseline approval)
| Window | Work |
|---|---|
| **H0–H12 Foundation Gate** | Lead (C assists on migration 001): G0.3–G0.9, §5 checklist, freeze, create worktrees |
| **H12–H36 Wave 1** | **A:** shell, navigation, Dashboard/Campaigns/Prospects against MSW mocks, backend-status indicator. **B:** PolicyEngine (hard eligibility + DEFER), suppression, idempotency, PostgreSQL job queue, transition service + audit, campaign/prospect APIs, approvals, kill switch, DBOS spike. **C:** repositories, audit trigger, seed policy v1, Qdrant collections + rebuild command, up/down/up test. **D:** Ollama + Fake providers, qwen3 structured-output eval, ToolGateway + scope matrix, Discovery/Qualification/Research/Contact agents on TestModel, run persistence. **E:** mock Search/Crawler/SEO/Email, ~20-site fixture corpus (eligible, ineligible, missing metrics, spammy, prompt-injection), canonicalization/dedupe, deterministic research and contact extraction, ManualImport SEO. **F:** metric catalog, daily/weekly aggregation, labelled ROI calculator, deterministic hashed snapshot builder. **QA:** E2E harness, contract tests. **Sec:** review auth handshake and Electron config. |
| **H36 checkpoint I1** | Lead merges; QA runs slice |
| **H36–H60 Wave 2** | **A:** Outreach, Replies, Backlinks, Approvals, Agent Runs, Reports, Settings (mode, kill switch). **B:** pre-send policy re-check, follow-up scheduling/cancellation, campaign-run workflow. **C:** indexes, memory store, rebuild test, backup script draft. **D:** Outreach agent (claims → evidence), Reply agent (deterministic opt-out/bounce rules first), Negotiation agent (risk → approval), Verification-ambiguity agent, Operator decision loop, eval datasets. **E:** outreach QA (evidence, similarity), idempotent mock send, reply simulation + ingestion, negotiation routing, opportunity/submission tracking, fixture-based verifier + rechecks. **F:** weekly report API + narrative input, outcome collection, feature snapshots, analyzers, proposal generator, offline test harness, promotion service with risk-class guards. |
| **H60 checkpoint I2** | Lead merges; QA runs slice |
| **H60–H72 Wave 3** | Full-loop fixture E2E in CI (fake model) and a local run on Ollama; QA and security reviews; phase-gate skill; `docs/phase-reports/mvp.md`; git checkpoint |

After MVP:
- **Days 4–8 (V1):** real providers behind flags and disabled by default (SearXNG, httpx/Crawl4AI, Gmail OAuth / IMAP+SMTP), shadow/canary path, exports, first-run setup, backup/restore, macOS packaging plus the backend/PostgreSQL/Qdrant bundling spike (ADR-0019), GA4/GSC providers if time allows.
- **Days 10–14 (RC):** security hardening, prompt-injection suite, performance/failure tests, provenance records (SEC-08), runbooks, Windows packaging if users are on Windows, release-check skill.

## 5. Foundation Gate acceptance criteria
- [ ] Work is on a `foundation/*` branch with a clean tree. `.claude/` (8 agents, 3 rules, 3 skills) and `.env.example` are present and validated. Canonical reference image in place. Hygiene done.
- [ ] ADRs written and indexed; registry has no duplicate IDs and a complete v0 mapping; `docs/*.md` reconciled.
- [ ] `uv sync` (Python 3.12) and `npm ci` succeed. ruff, mypy (strict on `kb_*`), pytest, eslint, tsc, vitest and import-linter pass.
- [ ] Compose dev stack healthy: PostgreSQL on 55432, Qdrant on `QDRANT_HOST_PORT` (default 6333).
- [ ] Migration 001 builds the full docs/04 schema; up/down/up test passes; audit trigger rejects UPDATE and DELETE.
- [ ] Contracts v0.1 in place; exhaustive invalid-transition tests pass for all six machines plus campaign.
- [ ] OpenAPI snapshot committed, TS client generated, drift check passes.
- [ ] API security: binds 127.0.0.1 only on a dynamic port; missing/wrong token → 401, correct → 200 (constant-time compare); CORS denies all origins; Host allowlist blocks DNS rebinding; token never appears in captured logs; correlation IDs propagated.
- [ ] `/health/ready` reports PostgreSQL, Qdrant and Ollama; Ollama absent = degraded, not failure.
- [ ] Electron spawns the backend, completes the handshake and shows health through the bridge; `contextIsolation`, `sandbox`, `nodeIntegration=false` and CSP set; a test proves the renderer cannot read the token.
- [ ] Defaults `REAL_SEND_ENABLED=false`, `AUTONOMY_MODE=shadow`; secret scan (gitleaks) clean.
- [ ] `infrastructure/scripts/check.sh` runs every gate locally; CI workflow needs no credentials and no Ollama.
- [ ] phase-gate skill run, QA and security reviews recorded, `docs/phase-reports/foundation.md` written, git checkpoint made.

## Dependencies that can extend delivery
Claude Code plan limits, local machine performance, OAuth setup, a legal zero-cost DA/Spam source, email-provider restrictions, desktop packaging (backend/PostgreSQL/Qdrant bundling), review turnaround and real-world provider behavior.
