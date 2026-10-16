# Phase Report — Foundation Gate (G0.3–G0.9)

Branch `foundation/g0-gate` · base `baa5a7e` · contracts version `0.1.0` · 2026-10-16
Executed by the Level-0 Lead. No child subagents were used for implementation; `qa-engineer` and `security-reviewer` were run as independent read-only reviewers (phase-gate steps 3–4).

## Verdict
**Foundation Gate: PASSED, with the explicit exceptions listed under "Not verified / deviations".** All local gates are green via `infrastructure/scripts/check.sh --smoke`. Lanes A–F have **not** been launched.

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

## Results (last full run, `check.sh --smoke`, exit 0)
| Gate | Result |
|---|---|
| ruff check / format | clean, 108 files |
| mypy strict (`kb_*`) | 41 files, no issues |
| import-linter | 3 contracts kept; **verified to fail on a deliberate violation** |
| pytest | **123 passed** (contract 64, security 40, integration 19) |
| vitest | **22 passed** |
| eslint / tsc | clean |
| contract drift | OpenAPI + TS in sync |
| requirements registry | 52 IDs, 0 duplicates, 20/20 v0 IDs mapped; 12 referenced by tests, 40 warn-only until MVP |
| real Electron smoke | `SMOKE_OK`: renderer has only the 3 bridge methods; `require`, `process`, `Buffer` undefined; backend spawned by main; `/health/ready` 200, `/health/live` 200; no orphan processes afterwards |

## Acceptance criteria (docs/10 §5)
| Criterion | Status |
|---|---|
| Branch, `.claude/`, `.env.example`, image, hygiene | ✅ |
| ADRs + registry + docs reconciled | ✅ |
| `uv sync` (3.12) | ✅ frozen lockfile |
| `npm ci` | ⚠️ `npm ci --dry-run` passes; a real `npm ci` has not been executed (see below) |
| ruff, mypy, pytest, eslint, tsc, vitest, import-linter | ✅ |
| Compose stack healthy | ✅ PostgreSQL 55432, Qdrant **56333** |
| Migration 001 full schema, up/down/up, audit trigger | ✅ |
| Contracts + exhaustive invalid-transition tests, all machines | ✅ plus independent golden tables |
| OpenAPI snapshot, TS client, drift check | ✅ |
| API security (loopback, dynamic port, 401/200, constant-time, CORS, Host, token not in logs, correlation IDs) | ✅ |
| `/health/ready` PG + Qdrant + Ollama; Ollama absent = degraded | ✅ |
| Electron spawns backend, handshake, health via bridge, hardening flags, renderer cannot read token | ✅ locally (real Electron + fake-IPC test); ❌ not in CI |
| Defaults `REAL_SEND_ENABLED=false`, `shadow` | ✅ now also fail-closed (see fixes) |
| Secret scan "gitleaks clean" | ⚠️ **not met as written**: gitleaks is not installed; the built-in pattern fallback ran clean |
| `check.sh` runs every gate; CI needs no credentials/Ollama | ✅ locally; ⚠️ CI workflow written but never executed |
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
1. **Qdrant host port is 56333, not 6333.** 6333 is held by the unrelated `ken-interlink-qdrant-1` container from the Internal Linking project; it was left untouched. Config, compose and docs were updated.
2. **Qdrant down ⇒ `degraded`, not failure** (derived/rebuildable per ADR-0003). The criterion only named Ollama explicitly; only PostgreSQL failure returns 503.
3. **Secret scan is the built-in fallback**, not gitleaks. It exempts `tests/` and known dev placeholders.
4. **CI has never run.** `.github/workflows/ci.yml` is unexecuted, uses tag-pinned actions, and omits `--smoke` (needs a display on Linux).
5. **`npm ci` not executed.** npm 11.20 gates dependency install scripts; Electron's binary was fetched lazily on first launch rather than at install. Vitest does not need it; smoke does.
6. **`ELECTRON_RUN_AS_NODE` is set in this VS Code environment**; the smoke script unsets it. Any other harness launching Electron must do the same. Smoke uses `env -u`, so it is macOS/Linux-only for now.
7. **Route stubs return 501 with untyped bodies**, so the generated TS client types only the health endpoints precisely. Lanes tighten response models via contract change requests.
8. **SecretStore and JobQueue are protocols only**; no Env/keyring or PostgreSQL queue implementation yet (Lane B).
9. **Contract changes made during review (pre-freeze):** message CANCELLED edges, workflow WAITING→FAILED. Version stays `0.1.0` because nothing consumed it yet.
10. `MONITORED` is documented as the steady state of a promoted version (its only exit is rollback).
11. Migration 001 was regenerated several times pre-release; the dev database was rebuilt from the final version (it held no data).

## Decisions needed before launching lanes
1. **Freeze contracts v0.1 as-is?** (Recommended: yes.)
2. **Install gitleaks** (and run CI once) to meet the "gitleaks clean" criterion literally?
3. **Defer H2 (role separation) and M8 (custom-protocol CSP) to V1 hardening** as recorded? (Recommended: yes.)
4. Whether to **push the branch / open a PR** so CI can run, since nothing has been pushed.

## Next step
On approval: freeze contracts, create the six lane worktrees (`lane/<x>-<name>`), and begin Wave 1 per docs/10 §4. Not started.
