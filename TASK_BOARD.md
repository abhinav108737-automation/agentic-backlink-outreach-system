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

## Wave 1 (toward integration checkpoint I1) — **Lanes C + E COMPLETE 2026-10-17; I1 not yet reached**
Lanes C and E are done and verified. Lanes B and D have not been launched, so the I1 slice does
not yet run end to end. Full gate green: `check.sh` exit 0 — ruff, strict mypy (73 files),
import-linter 3/3, **315 pytest (151 unit + 60 DB integration, 0 skipped) + 22 vitest**,
contract drift in sync, gitleaks clean.
Detail and resume plan: `docs/phase-reports/wave1-handoff.md`.
- [x] Lead: repository/UnitOfWork seam `kb_core/db/protocols.py` (frozen for Wave 1; now exercised from the data side and satisfied by `SqlUnitOfWork` under strict mypy)
- [x] Lead: CCR-001 filed + approved (`docs/ccr/CCR-001.md`) — **not yet executed** (needs Lane B)
- [x] **Lead: CCR-002 FILED, NOT APPLIED (`docs/ccr/CCR-002.md`) — needs a user decision.** `DomainMetric.value` is a non-optional `float` while the DB column, `SEOMetric` and the repository signature are all nullable. Worked around with a `NaN` sentinel, which fails safe for thresholds but cannot JSON round-trip. **Must be resolved before Lane B exposes metrics through the API.**
- [x] Lane C data: all 18 repositories, `SqlUnitOfWork` + factory (transition+audit atomic in one transaction), seed policy v1 (`sha256:e6e54ac6…b71b48`), Qdrant provider + in-memory provider + rebuild command, 60 DB integration tests incl. append-only triggers, suppression guard, optimistic lock, metric three-state, UoW rollback, idempotency race, `FOR UPDATE SKIP LOCKED` job race, schema-drift reflection
- [x] Lane E outreach: 21-site fixture corpus (all 5 label classes), MockSearch/MockCrawler/MockSEOMetrics, ManualImport CSV, canonicalization + dedupe + stable `url_hash`, deterministic research and contact extraction, 151 offline unit tests incl. the mandatory ADR-0020 prompt-injection inertness suite
- [ ] Lane B backend: **nothing landed.** PolicyEngine, hard eligibility + DEFER, suppression, idempotency, PostgresJobQueue, TransitionService, campaign/prospect APIs, approvals, kill switch
- [ ] Lane D agent runtime (never launched): Ollama + Fake providers, ToolGateway + scope enforcement, Discovery/Qualification/Research/Contact agents, run persistence
- [ ] Lead: CCR-001 (+ CCR-002 if approved) execution, I1 pipeline wiring, I1 checkpoint commit

### Known limitations accepted in Wave 1 (not defects to rediscover)
- **No Public Suffix List.** Registered-domain extraction uses a hand-maintained multi-label suffix set (`.co.uk`, `.com.au`, `.co.nz`, `.co.in`, `.co.jp`, `.com.br`, `.com.cn`, `.com.sg`, …). Known-wrong cases are pinned in tests: unlisted suffixes (`shop.example.com.ng` → `com.ng`) and private suffixes (`alice.github.io` → `github.io`, which would collapse distinct prospects onto one registered domain). No IDN/punycode normalisation.
- **Injection detection is per text node.** A `mailto:` in a clean node adjacent to an injected node still yields a contact *candidate*. A candidate triggers no action and the deterministic gates still apply.
- **Obfuscated / JS-rendered email addresses are not extracted** (no browser provider in this wave).
- **Qdrant rebuild is tested against the in-memory provider only**; the CLI was not run against live Qdrant or Ollama.

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
