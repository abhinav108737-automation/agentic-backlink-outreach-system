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

## Wave 1 (toward integration checkpoint I1) — **Lanes B, C, D, E ALL COMPLETE 2026-10-18; I1 integration not yet done**
All four Wave 1 lanes are done and verified. **Contracts are now `0.2.0`** (CCR-001 + CCR-002
bundled into one revision). What remains for I1 is Lead integration wiring: the ToolGateway
capabilities are fakes, so the slice does not yet run end to end through real agents.
Full gate green: **`check.sh` exit 0** — ruff, ruff format (248 files), strict mypy (140 files),
import-linter 3/3, **786 pytest (591 unit incl. 194 agents, 83 DB integration, 72 contract,
40 security; 0 skipped) + 22 vitest**, contract drift in sync, gitleaks clean.
Detail and resume plan: `docs/phase-reports/wave1-handoff.md`.
- [x] Lead: repository/UnitOfWork seam `kb_core/db/protocols.py` (frozen for Wave 1; now exercised from the data side and satisfied by `SqlUnitOfWork` under strict mypy)
- [x] **Lead: CCR-001 + CCR-002 EXECUTED as one revision → contracts `0.2.0`.** `DomainMetric.value` is now `float | None`; `Field(allow_inf_nan=False)` on both metric DTOs so NaN/inf are rejected, not merely unused; NaN sentinel deleted; OpenAPI (2→37 schemas, 25 paths) and TS client regenerated; drift gate clean; single `contracts/CHANGELOG.md` entry. No migration needed — the column was already nullable.
- [x] Lane C data: all 18 repositories, `SqlUnitOfWork` + factory (transition+audit atomic in one transaction), seed policy v1 (`sha256:e6e54ac6…b71b48`), Qdrant provider + in-memory provider + rebuild command, 60 DB integration tests incl. append-only triggers, suppression guard, optimistic lock, metric three-state, UoW rollback, idempotency race, `FOR UPDATE SKIP LOCKED` job race, schema-drift reflection
- [x] Lane E outreach: 21-site fixture corpus (all 5 label classes), MockSearch/MockCrawler/MockSEOMetrics, ManualImport CSV, canonicalization + dedupe + stable `url_hash`, deterministic research and contact extraction, 151 offline unit tests incl. the mandatory ADR-0020 prompt-injection inertness suite
- [x] Lane B backend: PolicyEngine (7 block codes, no short-circuit), hard eligibility with state-2/state-3 DEFER, ranking, TransitionService (frozen-table check → guards → optimistic version bump → audit in the same transaction), suppression, idempotency, audit service, `UowJobQueue` + `SystemClock`/`FakeClock` + bounded `drain_once`/`run_iterations`, kill switch, approvals, live campaign/prospect/approvals/settings/system APIs with problem+json errors
- [x] Lane D agent runtime: Fake + Ollama model and embedding providers, ToolGateway with 9-stage checking and fail-closed auditing, per-agent scope enforcement, Discovery/Qualification/Research/Contact agents on typed outputs, agent-run persistence, prompt-injection eval
- [ ] **Lead: I1 integration wiring + I1 checkpoint commit.** Register real capabilities behind the ToolGateway fakes (repos, crawler, search, PolicyEngine gate), then prove discover → qualify (incl. a DEFERRED prospect) → research → contact end to end through the API.

### Known limitations accepted in Wave 1 (not defects to rediscover)
- **No Public Suffix List.** Registered-domain extraction uses a hand-maintained multi-label suffix set (`.co.uk`, `.com.au`, `.co.nz`, `.co.in`, `.co.jp`, `.com.br`, `.com.cn`, `.com.sg`, …). Known-wrong cases are pinned in tests: unlisted suffixes (`shop.example.com.ng` → `com.ng`) and private suffixes (`alice.github.io` → `github.io`, which would collapse distinct prospects onto one registered domain). No IDN/punycode normalisation.
- **Injection detection is per text node.** A `mailto:` in a clean node adjacent to an injected node still yields a contact *candidate*. A candidate triggers no action and the deterministic gates still apply.
- **Obfuscated / JS-rendered email addresses are not extracted** (no browser provider in this wave).
- **Qdrant rebuild is tested against the in-memory provider only**; the CLI was not run against live Qdrant or Ollama.
- **ToolGateway capabilities are fakes.** No real repository, crawler or search is wired behind them; `kb_agents/testing/fakes.py` holds the stand-ins. This is a deliberate consequence of the import boundary (`kb_agents` may not import `kb_core.db` or the crawler), and wiring them is Lead I1 work.
- **Two Ollama embedders exist** — `kb_providers/vector/embeddings.py` (Lane C) and `kb_providers/embedding/ollama.py` (Lane D). Real duplication; deduping is deferred rather than done mid-wave.
- **`LOST → ELIGIBLE` fails closed** with `RECONTACT_COOLDOWN_UNDEFINED`: `PolicyBody` has no cooldown field (TBD).
- **Idempotency keys are honoured on campaign create only.** Approval decide and campaign pause/activate are safe on repeat via 409 instead.
- **No HTTP routes for suppression add/supersede** — the service exists and is tested, but is not exposed.
- **No seeded kill-switch row**; absent means off. A malformed row counts as active (fail closed).
- **`policies/**` is empty** — policy YAML is a backlog item, not used.

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
