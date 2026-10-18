# Session Handoff — Wave 1 (toward integration checkpoint I1)

Date 2026-10-18 · branch `foundation/g0-gate` · tree clean · contracts **0.2.0**
History: `baa5a7e` (canonical docs + ADRs) → `84af80f` (Foundation Gate accepted) →
`fc424ab` / `74c517d` / `8d246e8` (interrupted first Wave 1 attempt, preserved) →
this commit (Lanes C + E complete).

## Status: all four Wave 1 lanes COMPLETE. I1 integration not yet done.

Lanes B, C, D and E are finished, tested and verified, and **contracts are now `0.2.0`**
(CCR-001 + CCR-002 bundled into one revision). The campaign and prospect APIs are live and a
DEFERRED prospect's reason is exposed through `GET /prospects/{id}`.

**What remains for I1 is Lead integration wiring, not lane work.** Every ToolGateway capability
is a fake: `kb_agents` may not import `kb_core.db` or the crawler (import-linter enforces it), so
the agents cannot reach real repositories, search or crawling until the Lead registers real
callables behind them. Until that happens the slice runs end to end *through the deterministic
services and API*, but **not through the agents**. No I1 checkpoint commit has been made, and
stopping before I1 integration was deliberate.

## Gate — green, `infrastructure/scripts/check.sh` exit 0

`infrastructure/scripts/check.sh` → **`CHECK_SH_EXIT=0`**

| Gate | Result |
|---|---|
| ruff check / format | clean, 248 files |
| mypy strict | no issues, **140 source files** |
| import-linter | 3/3 contracts kept |
| pytest | **786 passed, 0 skipped** — 591 unit (194 agents), 83 DB integration, 72 contract, 40 security |
| vitest | 22 passed |
| contract drift | OpenAPI + TS **in sync at `0.2.0`** |
| gitleaks | clean, history + working tree |

Test count across the wave: **123 → 315 → 786**.

**A note on how this gate was verified.** An earlier run of mine piped `check.sh` through `grep`,
so the reported exit code was grep's, not the gate's — it printed 0 while pytest had a failure.
The gate is now run redirecting to a log and reading `$?` directly. If you script this, do not
pipe `check.sh`; capture its exit code.

## Independently verified by the Lead (not taken on agent report)

| Claim | Verification |
|---|---|
| Metric three-state through the real UoW | `latest()` returned `{domain_authority: 55.0, spam_score: None}` with `trust_flow` **absent** — all three states distinct |
| UoW transition+audit atomicity | audit row visible inside the transaction (1), **gone after rollback** (0) |
| Seed `content_hash` | recomputed independently, matches `sha256:e6e54ac6b44bdb3901e6569b6ac41bb8b2b1c422e475c843cfe14c99f7b71b48` |
| Four DEFER shapes | 3 fetched-but-unavailable + 1 never-fetched all DEFER; only a fully-metricked domain passes |
| Prompt-injection inertness | adversarially re-tested across all three injection sites and every declared page; no bait text reaches topics/title/evidence, no `attacker.example` contact |
| Tool-scope enforcement (Lane D) | probed adversarially: executable capabilities registered under **all 7 forbidden names**, **0 executions** across 70 agent×capability combinations; out-of-scope tools never reach a capability; `OUTREACH` refused both `send_email_direct` and `request_send`; 313 calls audited |
| DEFER matrix (Lane B) | verified directly: state 2 → `DEFERRED` + `METRIC_UNAVAILABLE` + key present as `null`; state 3 → `DEFERRED` + `METRIC_NEVER_FETCHED` + key omitted; a missing metric **dominates** a threshold failure; `on_missing_metric="pass"` rejected at construction |
| No NaN on the wire | OpenAPI strict-parsed with `parse_constant` rejecting literals — clean; spec round-trips under `allow_nan=False`; `MetricReading.value` is `number \| null` |
| Import boundary (Lane D) | `kb_agents` imports only `kb_core.{contacts,discovery,research}` — never `kb_core.db`, crawler, email or browser |

Two apparent injection failures in the Lead's first probe were artifacts of the probe itself:
"injected" appears in `injected-editorial-notes.com`'s own domain name, and that site's payload
lives only on `/`, so `injection_suspected: False` on `/write-for-us` is correct.

## What exists now

**Lane C** — `kb_core/db/`: `_common.py`, `repos_core.py` (audit, policy, campaign, domain,
domain_metric, prospect, prospect_url), `repos_research.py` (qualification, ranking, evidence,
dossier, contact, suppression), `repos_runtime.py` (idempotency, job, approval, settings,
agent_run), `uow.py`, `seed.py`, `vector_rebuild.py`, plus `tables.py` (48 tables; PK
`server_default gen_random_uuid()` added to stop a per-insert SQLAlchemy PK warning — metadata
only, no schema change, and test (i) reflects the live DB to prove it).
`kb_providers/vector/`: `qdrant.py` (httpx REST), `memory.py`, `embeddings.py`.
All 18 repositories exist, so `SqlUnitOfWork` genuinely satisfies the frozen `UnitOfWork`
protocol — checked by mypy through two annotated functions in `uow.py`.

**Lane E** — `kb_providers/seo_metrics/` (`csv_io.py` strict parser, `mock.py`,
`manual_import.py`), `kb_core/discovery/canonical.py`, `kb_core/research/`
(`htmlscan.py`, `untrusted.py`, `extract.py`), `kb_core/contacts/extract.py`.

**Lane B** — `kb_core/`: `errors.py`, `audit/`, `policy/engine.py`, `qualification/{evaluator,service}.py`,
`ranking/`, `transitions/{guards,service}.py`, `suppression/{normalize,service}.py`,
`idempotency/`, `scheduling/{clock,queue,worker}.py`, `settings/`, `approvals/`, `campaigns/`,
`prospects/{views,service}.py`, `testing/{fakes,scenario}.py`.
`apps/api/`: `deps.py`, `schemas/`, plus live handlers in `routes/{campaigns,prospects,approvals,settings,system}.py`.
`create_app` gained optional `uow_factory` and `clock`.

**Lane D** — `kb_providers/model/{fake,ollama,schema_gen}.py`, `kb_providers/embedding/{fake,ollama}.py`.
`kb_agents/`: `model_bridge.py`, `tools/{specs,gateway,audit}.py`, `runs/{protocol,lifecycle,memory}.py`,
`prompts/`, `agents/{base,discovery,qualification,research,contact}.py`, `testing/fakes.py`.
`evals/{injection_eval.py,cases/}`.

## Behaviours any future lane must know

These are real and not in the protocol docstrings:

1. **`SettingsRepository.get` returns `{"key", "value", "version"}`**, not a bare value dict —
   otherwise `expected_version` could never be learned. For `set()`: `expected_version=None` is
   an unconditional upsert, `0` means "must not exist", an int is compare-and-set.
2. **`DomainMetricRepository.latest` is the authoritative three-state reader.** Use it for
   qualification. Do not read `DomainMetric.value` for availability decisions (see CCR-002).
3. **`QualificationRepository.record` rejects `ELIGIBLE` with non-empty `missing_metrics`** — a
   deliberate ADR-0015 backstop at the data layer.
4. **`ContactRepository.add_channel` raises `ValueError`** if `value_normalized` is not already
   `lower(btrim(...))`. `SuppressionRepository.add` normalises in SQL instead.
5. **Job queue semantics:** `complete(RETRY_WAIT)` increments attempts and sets bounded backoff
   (30s doubling, capped 1h) on the DB clock; attempts ≥ `max_attempts` → `DEAD`;
   `reclaim_expired` also → `DEAD` when attempts are spent; `claim_due` first promotes elapsed
   `RETRY_WAIT` jobs to `SCHEDULED` and claims at most one job per `concurrency_key`.
6. **Naive datetimes are rejected** (`ValueError`) wherever a datetime is accepted.
   `created_at`/`updated_at` use `clock_timestamp()`, not `now()`, so ordering inside one
   transaction is deterministic.
7. **`SqlUnitOfWork` is single-use**; re-entering raises `RuntimeError`. `uow.session` is not on
   the protocol — seed/rebuild/tests only.

## Contract revision 0.2.0 — both CCRs closed

**CCR-001 and CCR-002 were bundled into one revision; the version bumped once.** See
`contracts/CHANGELOG.md` and the execution records in `docs/ccr/`.

- **CCR-002:** `DomainMetric.value` is now `float | None`, and the `float("nan")` sentinel is
  gone. Writing the guard test showed that nullability alone does **not** forbid a sentinel —
  Pydantic accepts `NaN` for a plain float field by default — so `Field(allow_inf_nan=False)` was
  added to **both** `DomainMetric.value` and `SEOMetric.value`. `None` is accepted; `NaN`/`inf`
  raise `ValidationError`. Both DTOs were constrained deliberately: this change's whole rationale
  is that the two must agree, and leaving one unconstrained would let the sentinel re-enter at
  the provider boundary. No migration — the column was already nullable.
- **CCR-001:** OpenAPI and TS client regenerated from the implemented app. **2 → 37 schemas**,
  25 paths. New: `/campaigns/{campaign_id}/activate`, `/system/kill-switch`. No path removed and
  no `operation_id` changed, so the revision is additive. Request bodies live in
  `apps/api/src/kb_api/schemas/`, so `kb_contracts` gained no new types.
- **Guard test:** `tests/contract/test_no_nan_sentinels.py` (8 tests). `test_version` now pins
  `0.2.0` — that pin is deliberate: it fails whenever `contracts/**` changes without the version
  moving, which is the CCR process asserting itself.

### Two stale `501` assertions that CCR-001 surfaced

Both used `501` as a stand-in for "the request reached the backend", not as a claim about 501:

1. `tests/security/test_api_security.py` asserted `GET /prospects == 501`. Lane B had kept that
   green by making **unconfigured persistence return 501**. That is wrong semantics — the route
   *is* implemented, the dependency is absent — and it would let a misconfigured production
   deploy report "not implemented" instead of failing loudly. Changed to **503**, and the test
   now asserts both halves: a genuine stub (`/providers`) still returns 501, and `/prospects`
   returns 503 rather than 200 or 501.
2. `apps/desktop/tests/backend.test.ts` asserted the same thing to prove the IPC proxy attached
   the token. It now asserts `/providers` → 501 **and** `/prospects` → 200, which proves token
   attachment on both sides of the stub boundary — strictly stronger than before.

## Known limitations accepted in Wave 1 (do not rediscover these as bugs)

1. **No Public Suffix List.** Registered-domain extraction uses a hand-maintained multi-label
   suffix set. Known-wrong cases are pinned in tests: unlisted suffixes
   (`shop.example.com.ng` → `com.ng`) and private suffixes (`alice.github.io` → `github.io`,
   which would collapse distinct prospects onto one registered domain). No IDN/punycode
   normalisation. Changing any canonicalization rule requires bumping
   `CANONICALIZATION_VERSION` **and a backfill**, because of `UNIQUE(prospect_id, url_hash)`.
2. **Injection detection is per text node.** A `mailto:` in a clean node adjacent to an injected
   node still yields a contact *candidate*. A candidate triggers no action and the deterministic
   gates still apply downstream.
3. **Obfuscated / JS-rendered email addresses are not extracted** (no browser provider yet).
4. **Qdrant rebuild is tested against the in-memory provider only.** The CLI
   (`python -m kb_core.db.vector_rebuild`) was never run against live Qdrant or Ollama, though
   Qdrant is up on 56333. Exit 0 = ok, 2 = degraded.
5. **CI is still externally unverified** (no Git remote, Foundation decision 4). Every "green"
   here means green on this machine only.

## Next steps — I1 integration (not started, stopped per instruction)

All four lanes are done. What is left is Lead wiring, and it is the only thing between here and
I1:

1. **Register real capabilities behind the ToolGateway fakes.** This is the critical path. The
   gateway takes injected callables; `kb_agents/testing/fakes.py` currently supplies them. The
   Lead must wire: reads (`read_campaign_state`, `read_policy`, `read_memory`, `search`,
   `fetch_page`, `read_domain_metrics`, `read_evidence`) and records (`record_candidates`,
   `record_assessment`, `record_evidence`, `record_dossier`, `record_contact_candidates`), plus
   the real `AgentRunRepository` as the `AgentRunStore` and `RunStoreRecorder` as the gateway's
   recorder. A `ToolPolicyGate` backed by the PolicyEngine is optional but wanted.
   Lane D defined the result shapes in `kb_agents/tools/specs.py` (`CampaignStateView`,
   `PolicyView`, `DomainMetricsView`, `SearchData`, `FetchPageData`, `EvidenceListData`,
   `RecordEvidenceAck`, `RecordAck`) — **the wiring must match those shapes**, and any mismatch
   found there is a Lead decision, not a lane fix.
2. **Prove the I1 slice end to end**: discover → qualify (**including a DEFERRED prospect**) →
   research → contact → exposed through the API, with complete audit history.
3. Only then make the **I1 checkpoint commit**, and stop before Wave 2.

Deferred, deliberately not done mid-wave: dedupe the two Ollama embedders; expose suppression
over HTTP; add a `PolicyBody` re-contact cooldown so `LOST → ELIGIBLE` stops failing closed.

## Unchanged release blockers (Foundation Gate)

DB role separation (blocks production / live-send) · custom-protocol CSP (blocks production
packaging) · Windows `ELECTRON_RUN_AS_NODE` handling (blocks Windows packaging). None block I1.
