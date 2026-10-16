# Implementation Task Board

Legend: `[x]` done and verified · `[~]` partially done (note says what remains) · `[ ]` not started.

## Epic 0 Foundation (Foundation Gate G0.3-G0.9: **ACCEPTED** 2026-10-16; see docs/phase-reports/foundation.md)
Contracts v0.1.0 are **FROZEN** — changes only via a Contract Change Request (docs/14, contracts/CHANGELOG.md).
- [x] E0.1 git/monorepo (uv workspace on Python 3.12 + npm workspaces; import-linter boundaries)
- [x] E0.2 Electron + React + TS shell (hardened window, preload bridge, real-Electron smoke verified)
- [x] E0.3 FastAPI skeleton (per-launch auth, Host/Origin guards, problem+json, route stubs for PRD §22)
- [x] E0.4 PostgreSQL + Alembic (migration 001: 48 tables, append-only triggers, up/down/up tested)
- [~] E0.5 Qdrant: dev container + readiness probe done; collections, indexing and rebuild command are Lane C
- [x] E0.6 Docker Compose (PostgreSQL 55432, Qdrant host port configurable via QDRANT_HOST_PORT, default 6333; dev only)
- [~] E0.7 config/secrets abstraction: typed Settings, redaction and SecretStore protocol done; Env/keyring implementations are Lane B
- [x] E0.8 logs/correlation IDs (structured JSON, redaction, X-Correlation-ID propagation)
- [x] E0.9 OpenAPI typed client (snapshot + generated TS + drift gate)
- [~] E0.10 CI gate: `check.sh --smoke` passes locally (incl. real `npm ci` + gitleaks 8.30.1); `.github/workflows/ci.yml` **never executed — no Git remote configured, so CI is externally unverified**. Run it on the first push, before any lane merge is trusted.

## Wave 1 (toward integration checkpoint I1) — **INTERRUPTED 2026-10-16, I1 NOT reached**
All three implementation agents stopped on an account session rate limit. Partial work is
committed in `fc424ab` and is green (ruff, strict mypy, import-linter, 123 pytest).
Resume plan and exact next actions: `docs/phase-reports/wave1-handoff.md`.
- [x] Lead: repository/UnitOfWork seam `kb_core/db/protocols.py` (frozen for Wave 1)
- [x] Lead: CCR-001 filed + approved (`docs/ccr/CCR-001.md`) — **not yet executed**
- [~] Lane C data: table metadata + async engine + CursorPage done; **repositories, UnitOfWork, seed policy v1, Qdrant collections/rebuild, DB tests all outstanding**
- [~] Lane E outreach: 21-site fixture corpus (all 5 label classes) + MockSearch + MockCrawler + html_to_text done; **SEOMetricsProvider, ManualImport CSV, canonicalization/dedupe, research extraction, contact extraction, all tests outstanding**
- [ ] Lane B backend: **nothing landed.** PolicyEngine, hard eligibility + DEFER, suppression, idempotency, PostgresJobQueue, TransitionService, campaign/prospect APIs, approvals, kill switch
- [ ] Lane D agent runtime (batch 2, never launched): Ollama + Fake providers, ToolGateway + scope enforcement, Discovery/Qualification/Research/Contact agents, run persistence
- [ ] Lead: CCR-001 execution (CONTRACT_VERSION -> 0.2.0, OpenAPI + TS regen, changelog), I1 pipeline wiring, I1 checkpoint commit

## Epic 1 Campaign/Data
- [ ] campaign schema/API/UI
- [ ] prospect schema/API/UI
- [ ] policy/versioning
- [ ] audit
- [ ] lifecycle state machine
- [ ] dashboard

## Epic 2 Discovery/Qualification/Research
- [ ] SearchProvider + adapter
- [ ] CrawlerProvider + adapter
- [ ] canonicalization/dedupe
- [ ] SEOMetricsProvider
- [ ] hard eligibility
- [ ] ranking
- [ ] research evidence
- [ ] UI

## Epic 3 Agentic
- [ ] ModelProvider + Ollama
- [ ] PydanticAI Operator
- [ ] nine specialist agents
- [ ] typed tools
- [ ] run persistence
- [ ] eval fixtures

## Epic 4 Outreach
- [ ] contacts
- [ ] EmailProvider + mock
- [ ] policy gate/idempotency
- [ ] suppression/opt-out
- [ ] scheduler/follow-ups
- [ ] reply ingestion/classification
- [ ] negotiation routing

## Epic 5 Verification/Reporting
- [ ] backlinks/verifier/rechecks
- [ ] funnel metrics
- [ ] weekly snapshots
- [ ] ROI
- [ ] dashboards/export

## Epic 6 Learning/Optimization
- [ ] outcomes/features/analyzers
- [ ] calibration/patterns
- [ ] experiments/proposals
- [ ] offline/shadow/canary
- [ ] promotion/rollback/UI

## Epic 7 Hardening/Packaging
- [ ] security/prompt injection
- [ ] **BLOCKER (production / live-send release)** DB role separation: non-owner `kb_app` role without TRIGGER/ALTER rights so append-only and suppression guard triggers cannot be altered or dropped by app SQL (security review H2; deferred to V1 hardening by decision 7, 2026-10-16)
- [ ] **BLOCKER (production packaging)** serve renderer from a custom protocol so CSP is a real header rather than a meta tag with `frame-ancestors` ignored (security review M8; deferred to V1 packaging/hardening by decision 8, 2026-10-16)
- [ ] **BLOCKER (Windows packaging)** Windows-compatible way to clear `ELECTRON_RUN_AS_NODE` in the desktop `smoke` script; `env -u` is the approved macOS/Linux workaround only (decision 9, 2026-10-16)
- [ ] CI hardening: SHA-pinned actions, npm audit / pip-audit, gitleaks, Electron smoke under xvfb
- [ ] Electron security
- [ ] performance/failure tests
- [ ] Windows/macOS packages
- [ ] upgrade/migration/runbooks
- [ ] final acceptance
