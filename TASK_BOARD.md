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
- [x] **Lead: I1 integration COMPLETE (2026-10-23) — see `docs/phase-reports/i1.md`.** `kb_core.agentic` wires all 12 approved capabilities to real repositories, deterministic services and fixture-backed mock providers; run persistence and tool-call auditing use the real `AgentRunRepository`; the deterministic PolicyEngine gates every write tool. The slice is agent-driven end to end: one observed run took `supplychainlens.io` to **CONTACT_READY** with 2 contacts, rejected a spammy domain, and DEFERRED two missing-metric domains. 12 E2E tests in `tests/e2e/`.

## Integration Checkpoint I1 — **PASSED 2026-10-23, STABILIZED 2026-10-23**
Checkpoint `a201b9e`; stabilization commit follows. Gate: `check.sh` **exit 0** (exit code read
directly, not through a pipe) — **813 pytest** (12 E2E + 15 import-boundary) + 22 vitest,
strict mypy 144 files (now incl. `apps/runtime/src`), **import-linter 5/5**, contracts in sync
at `0.2.0` (unchanged by I1 or stabilization), gitleaks clean.
Agent containment proven against the real wired gateway: all 7 forbidden capabilities refused
and audited, an out-of-scope wired tool refused, nothing written on refusal, `fetch_page` limited
to http(s), no policy override, no direct email send.
- [x] I1: Discovery → Qualification → Research → Contact → persistence → audit → API readback
- [x] Three metric states preserved through the gateway; all four missing-metric cases DEFER
- [x] **Stabilization: architectural inversion removed.** The wiring moved from `kb_core.agentic` to a new leaf package `apps/runtime/` → `kb_runtime`, so `kb_core` no longer depends on `kb_agents`. Two new import-linter contracts (`core does not import the agent layer above it`, `agents do not import the composition root`), `kb_runtime` added to `root_packages`, and 15 AST-based boundary tests in `tests/contract/test_import_boundaries.py` that also see function-level imports, so a deferred import cannot hide a forbidden dependency. The new rule was proven non-vacuous by injecting a violation and watching it break. All 12 agent-driven I1 tests pass unchanged.
- [x] **Wave 2A COMPLETE 2026-10-25 — contracts `0.3.0`.** Gate `check.sh` **exit 0** (read directly): **1406 pytest** + 22 vitest, strict mypy 180 files, import-linter 5/5, in sync at `0.3.0`, gitleaks clean. See `docs/phase-reports/wave2a.md`.
  - Lead: outreach repositories added to the seam (**18 → 25**), unblocking both lanes; CCR-003 executed (8 paths added, none removed, 37 → 57 schemas).
  - Lane B: the five previously unemitted send block codes (`SENDER_INVALID`, `DUPLICATE_STEP`, `DUPLICATE_CONTACT`, `SEND_LIMIT_EXCEEDED`, `UNSUPPORTED_CLAIM`), durable job types + payloads, `SendService` with a six-outcome `begin_send` incl. crash `RECONCILE`, atomic terminal-sequence job cancellation + suppression, I2 endpoints where `sendMessage`/`verifyBacklink` return **202 and enqueue**.
  - Lane E: evidenced drafts + deterministic QA, `MockEmailProvider` idempotent by key, ordered reply classification (8 rules, opt-out/bounce never overridable), negotiation forcing approval on binding/high-risk, `PUBLISHED → EXPECTED` backlink, simulated verification with `LOST` after 3 misses and **only `VERIFIED` active**.
- [~] **Wave 2B PARTIAL 2026-10-25 — contracts `0.4.0` (CCR-004). I2 is NOT reached.** Gate `check.sh` **exit 0** (read directly): **1636 pytest, 1 skipped** + 22 vitest, strict mypy 188 files, import-linter 5/5, in sync at `0.4.0`, gitleaks clean. See `docs/phase-reports/i2.md`.
  - [x] CCR-004: `PolicyBody` + 5 follow-up/window fields. **No migration** (JSONB body), **no new API surface** (`PolicyBody` is in zero OpenAPI schemas). The 24h send window is no longer a hidden constant — it is `send_window_hours`, read from versioned policy, and explicitly **not** grandfathered as an approved rule.
  - [x] Typed job registry (`kb_runtime.jobs`): frozen constants bound to pre-built closures; no callable name, module path or import ever comes from a payload; a declared-but-unbound type is a startup error. Covers all 7 types exactly.
  - [x] Composition root + bounded worker (`python -m kb_runtime.worker --iterations N`). FastAPI still only enqueues.
  - [x] The 3 previously unwired capabilities: `read_thread`, `record_classification`, `record_negotiation_plan`.
  - [x] Reply + Negotiation agents, bounded. Deterministic class is **copied, never taken from the model**; a mismatch fails the run before anything is recorded. `makes_binding_commitment` is a one-way ratchet. Agent-side commitment mirror pinned to core's regex, and the drift test proven non-vacuous.
  - [x] Follow-up delays from policy measured from the previous *successful* send; timezone-aware sending window with DST cases; pre-dispatch re-check of policy/approval/idempotency/sender.
  - [x] **Lead correction:** campaign pause and kill switch now **hold** the sequence (timer re-armed, audited) instead of cancelling it terminally. Cancelling would have destroyed every in-flight sequence on a reversible operator action. Suppression still cancels. A test proves a held sequence resumes after reactivation.
  - [ ] **I2 acceptance suite is 2 of 14.** Passing: the chain CONTACT_READY → mock send, and "no email was ever transmitted". Skipped with the reason in code: the reply-onward half. **12 integration-level tests still to write** — listed in `docs/phase-reports/i2.md`.
- [ ] **I2 checkpoint (next session):** finish the reply→verify half of the chain test and the remaining 12 acceptance tests, then make the I2 checkpoint commit

### Open items carried out of Wave 2A (decided, not forgotten)
- **Follow-up delay has no home in `PolicyBody`.** `max_follow_ups` exists but no interval field. Lane E's `outreach.follow_up` handler schedules the next step; the *interval* is still TBD and adding a field needs a CCR. Do not invent one.
- **`OUTREACH_READY → IN_OUTREACH` is wired** by Lane E's delivery service after SENT. Confirmed present.
- **Prospect `WON`/`LOST` on opportunity outcome is not wired** — no spec item asked for it.
- **No Reply or Negotiation agent.** Classification is fully deterministic; `unknown` escalates to a human. The agents are Wave 2B.
- **Two repository lookups missing**: opportunity-by-sequence and sequences-by-prospect. Both are currently paged over; fine at this scale, worth repository methods later.
- **Per-domain and per-contact send windows are 24h**, same as `daily_send_limit`. `PolicyBody` specifies no window; 24h is the Lead's confirmed choice (a calendar day would allow a double burst across midnight). One constant, `SEND_WINDOW`.
- **A campaign cannot send without an enabled email `provider_accounts` row** set as its `sender_provider_account_id`. A fresh database has none, so `SENDER_INVALID` fires by default — intended fail-closed behaviour.

### Known limitations accepted in Wave 1 (not defects to rediscover)
- **No Public Suffix List.** Registered-domain extraction uses a hand-maintained multi-label suffix set (`.co.uk`, `.com.au`, `.co.nz`, `.co.in`, `.co.jp`, `.com.br`, `.com.cn`, `.com.sg`, …). Known-wrong cases are pinned in tests: unlisted suffixes (`shop.example.com.ng` → `com.ng`) and private suffixes (`alice.github.io` → `github.io`, which would collapse distinct prospects onto one registered domain). No IDN/punycode normalisation.
- **Injection detection is per text node.** A `mailto:` in a clean node adjacent to an injected node still yields a contact *candidate*. A candidate triggers no action and the deterministic gates still apply.
- **Obfuscated / JS-rendered email addresses are not extracted** (no browser provider in this wave).
- **Qdrant rebuild is tested against the in-memory provider only**; the CLI was not run against live Qdrant or Ollama.
- **ToolGateway capabilities are now really wired** (I1). Remaining stand-in: `read_memory` returns no snippets because MemoryService is Wave 2 — deliberate, so an agent is exercised against empty memory rather than invented snippets.
- ~~`kb_core` depends on `kb_agents`~~ — **resolved.** `kb_runtime` (`apps/runtime/`) is now the composition root and a leaf; nothing imports it. Enforced by two new import-linter contracts plus boundary tests.
- **When the API must eventually trigger agent runs (Wave 2+), do NOT relax `kb_api → kb_agents`.** Enqueue on the PostgreSQL job queue and let a separate worker process import `kb_runtime` (ADR-0008/0017). `GET /agent-runs` readback needs repositories only, so it needs no exception either.
- **Two fixture domains are not registered domains** (`blog.datapulseanalytics.io`, `www.energyoutlookdaily.com`) while their metrics are keyed by host, so they DEFER as never-fetched. That is correct under ADR-0015; a subdomain-guessing fallback was deliberately NOT added because it could attribute one host's metrics to another.
- **Two Ollama embedders exist** — `kb_providers/vector/embeddings.py` (Lane C) and `kb_providers/embedding/ollama.py` (Lane D). Real duplication; deduping is deferred rather than done mid-wave.
- **`LOST → ELIGIBLE` fails closed** with `RECONTACT_COOLDOWN_UNDEFINED`: `PolicyBody` has no cooldown field (TBD).
- **Idempotency keys are honoured on campaign create only.** Approval decide and campaign pause/activate are safe on repeat via 409 instead.
- **No HTTP routes for suppression add/supersede** — the service exists and is tested, but is not exposed.
- **No seeded kill-switch row**; absent means off. A malformed row counts as active (fail closed).
- **`policies/**` is empty** — policy YAML is a backlog item, not used.

## Epic 8 Digital PR / Earned Media channel (PROPOSED — planning only, nothing implemented)
Architecture proposal complete 2026-10-25; **no code, no contract change, no migration**. Contracts
remain frozen at `0.4.0`. Sequenced strictly **after I2** so Wave 2B is not disturbed.
Plan: `docs/15_DIGITAL_PR_EXTENSION.md` · ADR-0021 (**Proposed**) · CCR-005 (**Proposed, not
approved**) · provenance: `docs/12_REPOSITORY_RESEARCH.md`.
- [x] Reference evaluated at pinned SHA `3ef97c52`, **license unverifiable** (`license: null`, no license file in the complete tree) ⇒ idea-level study only, **nothing copied**
- [x] Capability gap analysis, component/responsibility matrix, architecture + data model delta, provider contracts, FR-33…FR-45 / SEC-09…SEC-10 / NFR-13, risk + license assessment, 16 mock E2E acceptance criteria, post-I2 integration plan, authorised-access preconditions
- [ ] **Decision needed:** accept or reject ADR-0021 and CCR-005. Nothing proceeds without it
- [ ] D1 contracts + migration `0002` (7 additive tables, 2 nullable `backlinks` columns)
- [ ] D2 providers (mock + ManualImport) + opportunity/ranking/expert-profile services; research asset + pitch services with sourcing gates
- [ ] D3 job handlers in the typed registry, capabilities in `kb_runtime`, Media Research + Pitch agents (deterministic authority preserved)
- [ ] D4 the 16 acceptance criteria; checkpoint **I3-PR**
- [ ] D5 channel-split reporting + pitch-outcome learning (only after the Wave 3 foundation exists)
- [ ] **Blocked until authorised:** any real Qwoted adapter. Preconditions in docs/15 §10. ManualImport is the only lawful path meanwhile

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
