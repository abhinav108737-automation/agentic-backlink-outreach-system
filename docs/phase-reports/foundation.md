# Phase Report — Foundation Gate (G0.3–G0.9)

Branch `foundation/g0-gate` · base `baa5a7e` · contracts version `0.1.0` (**FROZEN**) · 2026-10-16
Includes the stabilization pass of 2026-10-16 that closed the gate's conditional items (see "Stabilization pass").
Executed by the Level-0 Lead. No child subagents were used for implementation; `qa-engineer` and `security-reviewer` were run as independent read-only reviewers (phase-gate steps 3–4).

## Verdict
**Foundation Gate: ACCEPTED.** Every conditional item from the first review is closed except one, which cannot be closed on this machine: **CI remains externally unverified** because the repository has no Git remote (decision 4 forbids inventing one). Contracts v0.1.0 are frozen. All local gates are green via `infrastructure/scripts/check.sh --smoke`, now including a real `npm ci` and gitleaks. Lanes A–F have **not** been launched.

## Deliverables
| Task | Delivered |
|---|---|
| G0.3 Scaffold | uv workspace (Python 3.12) with 5 packages, npm workspaces (`@ken/contracts`, `@ken/desktop`), ruff / mypy-strict / pytest / import-linter / ESLint / tsc / Vitest, `compose.dev.yml` |
| G0.4 Contracts v0.1 | `kb_contracts`: C1 enums + 9 state-machine tables, C2 DTOs, C3 agent I/O, C4 tool allowlist, C5 provider protocols + error taxonomy, C6 policy gate (14 block codes), C7 events, C11 settings, C13 logging/redaction; C8 API, C9 TS client, C10 Electron bridge, C12 fixture formats (`fixtures/README.md`) |
| G0.5 Migration 001 | 48 tables matching docs/04 exactly; append-only triggers; guarded-update triggers; duplicate-send and suppression constraints; up/down/up tested |
| G0.6 API | FastAPI factory, per-launch bearer auth, Host/Origin guards, correlation IDs, problem+json, `/health/live` + `/health/ready`, stdin handshake entrypoint, PRD §22 route stubs (501) |
| G0.7 Electron | hardened window, minimal preload bridge, IPC proxy with token held only in main, React health screen |
| G0.8 Codegen | deterministic OpenAPI snapshot, generated TS client, drift gate (verified to detect injected drift) |
| G0.9 Gate | `check.sh`, registry check, secret scan, CI workflow |

## Results (stabilization run, `check.sh --smoke`, exit 0, 2026-10-16)
| Gate | Result |
|---|---|
| ruff check / format | clean, 109 files |
| mypy strict (`kb_*`) | 41 files, no issues |
| import-linter | 3 contracts kept; **verified to fail on a deliberate violation** |
| pytest | **123 passed** (contract 64, security 40, integration 19) |
| vitest | **22 passed** |
| eslint / tsc | clean |
| contract drift | OpenAPI + TS in sync |
| requirements registry | 52 IDs, 0 duplicates, 20/20 v0 IDs mapped; 12 referenced by tests, 40 warn-only until MVP |
| secret scan | **gitleaks 8.30.1 clean** — history (5 commits) + working tree, scope narrowed by `.gitleaks.toml` |
| `npm ci` | **real install from a wiped `node_modules`, exit 0** |
| dev stack | PostgreSQL + Qdrant healthy with the host port taken from `QDRANT_HOST_PORT` |
| real Electron smoke | `SMOKE_OK`: renderer has only the 3 bridge methods; `require`, `process`, `Buffer` undefined; backend spawned by main; `/health/ready` 200, `/health/live` 200; no orphan processes afterwards |

## Acceptance criteria (docs/10 §5)
| Criterion | Status |
|---|---|
| Branch, `.claude/`, `.env.example`, image, hygiene | ✅ |
| ADRs + registry + docs reconciled | ✅ |
| `uv sync` (3.12) | ✅ frozen lockfile |
| `npm ci` | ✅ real `npm ci` executed from a wiped `node_modules`, exit 0 |
| ruff, mypy, pytest, eslint, tsc, vitest, import-linter | ✅ |
| Compose stack healthy | ✅ PostgreSQL 55432; Qdrant host port via `QDRANT_HOST_PORT` (default 6333, **56333** on this machine) |
| Migration 001 full schema, up/down/up, audit trigger | ✅ |
| Contracts + exhaustive invalid-transition tests, all machines | ✅ plus independent golden tables |
| OpenAPI snapshot, TS client, drift check | ✅ |
| API security (loopback, dynamic port, 401/200, constant-time, CORS, Host, token not in logs, correlation IDs) | ✅ |
| `/health/ready` PG + Qdrant + Ollama; Ollama absent = degraded | ✅ |
| Electron spawns backend, handshake, health via bridge, hardening flags, renderer cannot read token | ✅ locally (real Electron + fake-IPC test); ❌ not in CI |
| Defaults `REAL_SEND_ENABLED=false`, `shadow` | ✅ now also fail-closed (see fixes) |
| Secret scan "gitleaks clean" | ✅ met as written: gitleaks 8.30.1 installed and clean (history + working tree) |
| `check.sh` runs every gate; CI needs no credentials/Ollama | ✅ locally; ⚠️ **CI workflow still never executed — no Git remote exists** |
| phase-gate, QA + security reviews, report, checkpoint | ✅ |

## Independent review outcomes
**Security review:** no CRITICAL. Findings and disposition:
| # | Finding | Disposition |
|---|---|---|
| H1 | Suppression/idempotency integrity not enforced in DB; double-send possible with a different key | **Fixed**: suppression guard trigger (no delete/truncate, only `superseded_*` updatable), lowercase CHECK, `one_live_message_per_step` partial unique index, `UNIQUE(sequence_id, step_no, message_version)`, 5 new tests |
| H2 | Append-only is trigger-only; the app role is the DB owner and can drop triggers | **Deferred (V1 hardening)**: needs a non-owner `kb_app` role. Recorded in docs/04 and TASK_BOARD Epic 7 |
| M3 | IPC proxy is a generic authenticated tunnel; no sender validation | **Fixed**: route-root allowlist + IPC sender check (`makeTrustSender`) + tests |
| M4 | `%2e%2e` / `%2f` encoding bypass | **Fixed** + tests |
| M5 | Env-controlled escape hatches in packaged builds | **Fixed**: honoured only when `!app.isPackaged` |
| M6 | Redaction gaps (compound keys, DSN passwords, quoted JSON) | **Fixed** + tests (and a no-over-redaction test for `author`) |
| M7 | `real_send_enabled` validator was a no-op | **Fixed**: refuses real send in shadow mode; runtime approval gate remains Lane B |
| M8 | CSP over `file://` relies on the meta tag; `frame-ancestors` ignored in meta | **Deferred (V1)**: serve renderer from a custom protocol. Added permission-check, `will-redirect`, `will-attach-webview` handlers |
| L9/L10 | CI supply chain, host-only allowlist, env passthrough, destroyed-window guard | **Partly fixed**: frozen lockfile enforced, `localhost` dropped from Host allowlist, destroyed-window guard. **Not done**: SHA-pinned actions, `npm audit`/`pip-audit`, env-passthrough minimisation |

**QA review:** all 9 state tables match docs/13 row by row; 48 tables match docs/04. Findings and disposition:
| # | Finding | Disposition |
|---|---|---|
| G1 | **Gate hole: gate passed with PostgreSQL down (10 skipped)** | **Fixed**: `KB_REQUIRE_DB=1` (set by `check.sh`) / `CI` makes unreachable DB a failure; reproduced before, verified after |
| D1–D3 | No step uniqueness; `policy_versions` not actually immutable; no suppression delete/case guard | **Fixed** + tests |
| D4 | Missing CANCELLED edges on pre-send messages; WAITING workflow could not fail | **Fixed** in contract and docs/13 (design correction) |
| D5 | State test compared the code against itself | **Fixed**: independent golden tables transcribed from docs/13 |
| D6 | Vacuous/weak tests (source-grep, unbounded readline, undrained stderr) | **Fixed**: behavioural `compare_digest` spy, timeout-bounded handshake read, stderr to file |
| D7 | Health probes unbounded and sequential | **Fixed**: 2 s hard timeout, concurrent, with a hang test |
| D8–D12 | Unfrozen `uv sync` fallback; UUIDv7 doc mismatch; registry check gaps; enum parity untested | **Fixed** (docs/04 reworded; registry malformed-row and mapping-target checks; enum parity test) |
| — | Electron hardening unproven in CI | **Open**: smoke is local-only |

## Not verified / deviations (read these)
1. **Qdrant host port is configurable, not fixed.** The container/service port is always 6333. The dev-compose host port comes from `QDRANT_HOST_PORT` (default 6333) and `QDRANT_URL` must match it. On this machine both are set to **56333** in a local, gitignored `.env`, because 6333 is held by the unrelated `ken-interlink-qdrant-1` container from the Internal Linking project, which was left untouched. 56333 is a local fact, not architecture.
2. **Qdrant down ⇒ `degraded`, not failure** (derived/rebuildable per ADR-0003). The criterion only named Ollama explicitly; only PostgreSQL failure returns 503.
3. **Secret scan is gitleaks** (8.30.1, installed via Homebrew). `secret-scan.sh` runs two passes — `gitleaks git` over commit history and `gitleaks dir` over the working tree — because a secret deleted in a later commit still leaks, and uncommitted files never reach the history pass. `.gitleaks.toml` extends the upstream default ruleset and only narrows *where* it looks (vendored deps, build output, lockfiles, generated client, verbatim reference docs). The built-in pattern fallback is retained for machines without gitleaks and labels itself as the weaker engine.
4. **CI has never run, and cannot be run from here.** The repository has **no Git remote** (`git remote -v` is empty) and decision 4 forbids inventing or creating one. `.github/workflows/ci.yml` therefore remains **externally unverified**: never executed, tag-pinned actions, and no `--smoke` step (needs a display on Linux). It must be run on the first push, before lane merges are trusted.
5. **`npm ci` executed for real.** `node_modules` (root and workspaces) was deleted and rebuilt from `package-lock.json`, exit 0. npm 11.20 still gates dependency install scripts, so Electron's binary is fetched lazily on first launch rather than at install; vitest does not need it and the smoke run after `npm ci` succeeded.
6. **`ELECTRON_RUN_AS_NODE` is set in this VS Code environment**; the smoke script unsets it with `env -u`, which is the approved workaround for macOS/Linux development (decision 9). `env -u` is not available on Windows `cmd`/PowerShell, so **a Windows-compatible way to clear `ELECTRON_RUN_AS_NODE` in the smoke script is required before Windows packaging** (recorded in TASK_BOARD Epic 7). Any other harness launching Electron must clear the variable too.
7. **Route stubs return 501 with untyped bodies**, so the generated TS client types only the health endpoints precisely. Lanes tighten response models via contract change requests.
8. **SecretStore and JobQueue are protocols only**; no Env/keyring or PostgreSQL queue implementation yet (Lane B).
9. **Contract changes made during review (pre-freeze):** message CANCELLED edges, workflow WAITING→FAILED, the `real_send_enabled` validator, and the `qdrant_url` default. Version stays `0.1.0` because nothing consumed it yet. **Contracts v0.1.0 are now frozen** (decision 1): `contracts/CHANGELOG.md` records the freeze, and further changes require a Contract Change Request per docs/14 §"Contract change process" — CCR to the Lead, version bump, changelog entry, OpenAPI + TS regeneration.
10. `MONITORED` is documented as the steady state of a promoted version (its only exit is rollback).
11. Migration 001 was regenerated several times pre-release; the dev database was rebuilt from the final version (it held no data).

## Stabilization pass (2026-10-16)
The gate was conditionally accepted; this pass executed only the named stabilization work. No
features were added and no lane was started.

| # | Decision | Outcome |
|---|---|---|
| 1 | Freeze contracts v0.1 as implemented | **Done.** `contracts/CHANGELOG.md` created with the `0.1.0 — FROZEN` entry; changes now require a CCR (docs/14). |
| 2 | Install and run gitleaks locally | **Done.** gitleaks 8.30.1; `secret-scan.sh` runs history + working-tree passes; `.gitleaks.toml` added; clean. |
| 3 | Run a real `npm ci` | **Done.** `node_modules` wiped and reinstalled from the lockfile, exit 0; the workspace reconstructs from a clean dependency state. |
| 4 | Run CI once if a safe remote exists | **Not possible.** No Git remote is configured; none was invented. **CI remains externally unverified.** |
| 5 | Make the Qdrant host port configurable | **Done.** `QDRANT_HOST_PORT` (default 6333) in `compose.dev.yml`; `qdrant_url` default is now `http://localhost:6333`; `.env.example`, docs and the contract test updated; 56333 kept as a local `.env` override only. |
| 6 | Keep health semantics (PG = readiness failure, Qdrant = degraded) | **Kept, verified.** `health.py` marks only PostgreSQL critical; tests assert Qdrant down ⇒ `degraded` / 200 and PostgreSQL down ⇒ 503. |
| 7 | Defer DB role separation to V1 hardening, as a live-send blocker | **Recorded** below and in TASK_BOARD Epic 7. |
| 8 | Defer custom-protocol CSP to V1 packaging/hardening, as a packaging gate | **Recorded** below and in TASK_BOARD Epic 7. |
| 9 | Keep `env -u ELECTRON_RUN_AS_NODE`; Windows handling required before Windows packaging | **Kept and recorded** (deviation 6, TASK_BOARD Epic 7). |
| 10 | No features, no lanes A–F during this pass | **Honoured.** Changes were limited to config, docs, one contract-test assertion, the secret-scan script and this report. |

## Release blockers recorded
These are deferred, not dismissed. Each is a hard gate on a specific release step.
1. **DB role separation (security H2) — blocks production / live-send release.** The application
   connects as the database owner and can therefore `ALTER`/`DROP` its own append-only and
   suppression guard triggers, so those protections are advisory against the app's own SQL. A
   non-owner `kb_app` role without `TRIGGER`/`ALTER` rights must exist before real sending is
   enabled. Tracked in TASK_BOARD Epic 7.
2. **Custom-protocol CSP (security M8) — blocks production packaging.** Over `file://` the CSP
   comes from a meta tag, so `frame-ancestors` is ignored and the policy is not a real header. The
   renderer must be served from a custom protocol with CSP sent as a header before a packaged
   release ships. Tracked in TASK_BOARD Epic 7.
3. **Windows-compatible smoke handling — blocks Windows packaging.** See deviation 6.
4. **CI execution — blocks trusting lane merges.** See deviation 4.

## Next step
Contracts are frozen and the Foundation Gate is accepted. The next step is to create the six lane
worktrees (`lane/<x>-<name>`) and begin Wave 1 per docs/10 §4. **Not started** — stopped here per
decision 10.
