# Session Handoff — Wave 1 (toward integration checkpoint I1)

Date 2026-10-17 · branch `foundation/g0-gate` · tree clean
History: `baa5a7e` (canonical docs + ADRs) → `84af80f` (Foundation Gate accepted) →
`fc424ab` / `74c517d` / `8d246e8` (interrupted first Wave 1 attempt, preserved) →
this commit (Lanes C + E complete).

## Status: Lanes C and E COMPLETE. I1 not yet reached.

The data layer and the discovery/research/contact layer are done, tested and verified. The I1
slice (discover → qualify → DEFERRED case → research → contact → API) does **not** yet run end
to end, because **Lane B (PolicyEngine, transitions, APIs) and Lane D (agent runtime) have not
been launched.** No I1 checkpoint commit has been made. Lanes B and D were deliberately left
unlaunched per instruction.

## Gate — green, `infrastructure/scripts/check.sh` exit 0

| Gate | Result |
|---|---|
| ruff check / format | clean, 155 files |
| mypy strict | no issues, **73 source files** |
| import-linter | 3/3 contracts kept |
| pytest | **315 passed, 0 skipped** — 151 unit, 60 DB integration, 104 contract/security |
| vitest | 22 passed |
| contract drift | OpenAPI + TS in sync (contracts untouched, still `0.1.0`) |
| gitleaks | clean, history + working tree |

The previous handoff's central warning is now closed: the Wave 1 code **has real automated
coverage**. Test count went 123 → 315. All 60 DB tests execute against real PostgreSQL under
`KB_REQUIRE_DB=1` with zero skips.

## Independently verified by the Lead (not taken on agent report)

| Claim | Verification |
|---|---|
| Metric three-state through the real UoW | `latest()` returned `{domain_authority: 55.0, spam_score: None}` with `trust_flow` **absent** — all three states distinct |
| UoW transition+audit atomicity | audit row visible inside the transaction (1), **gone after rollback** (0) |
| Seed `content_hash` | recomputed independently, matches `sha256:e6e54ac6b44bdb3901e6569b6ac41bb8b2b1c422e475c843cfe14c99f7b71b48` |
| Four DEFER shapes | 3 fetched-but-unavailable + 1 never-fetched all DEFER; only a fully-metricked domain passes |
| Prompt-injection inertness | adversarially re-tested across all three injection sites and every declared page; no bait text reaches topics/title/evidence, no `attacker.example` contact |
| Contracts untouched | `git status contracts/` empty; `CONTRACT_VERSION = "0.1.0"` |

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

**Lane B** — nothing. **Lane D** — never launched.

## Behaviours Lane B must know before it writes a line

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

## Open contract decisions

- **CCR-001** (`docs/ccr/CCR-001.md`) — approved, **not executed**. Typed campaign/prospect/
  approvals/system routes; needs Lane B's handlers to exist first. The contract drift gate will
  fail between Lane B landing handlers and the Lead regenerating — expected, and the Lead's job.
- **CCR-002** (`docs/ccr/CCR-002.md`) — **FILED, NOT APPLIED. Needs a user decision.**
  `DomainMetric.value: float` is non-optional while the DB column, `SEOMetric.value` and the
  repository signature are all nullable. Lane C bridged it with a `NaN` sentinel: safe against
  thresholds (NaN fails every comparison) but it cannot JSON round-trip — Pydantic emits
  `"value":null`, which then fails validation on the way back, and strict JSON refuses NaN
  outright. **Blast radius is nil today** (all routes are still 501 stubs, nothing serializes
  `DomainMetric`) but this must be resolved **before Lane B exposes metrics through the API**.

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

## Next steps (not started — stopped per instruction)

1. Decide CCR-002.
2. Launch **Lane B** (PolicyEngine, hard eligibility + DEFER, suppression, idempotency,
   PostgresJobQueue, TransitionService, campaign/prospect APIs, approvals, kill switch) against
   the now-real repository seam. Brief it with the seven behaviours above.
3. Launch **Lane D** (Ollama + Fake model providers, ToolGateway + scope enforcement, the four
   agents on TestModel, run persistence).
4. Lead: execute CCR-001 (+ CCR-002 if approved) — `CONTRACT_VERSION` → `0.2.0`,
   `uv run python -m kb_api.export_openapi`, `npm run gen:contracts`,
   `infrastructure/scripts/check-contracts.sh`, `contracts/CHANGELOG.md`.
5. Wire and prove the I1 slice including a DEFERRED prospect, then make the I1 checkpoint commit.

## Unchanged release blockers (Foundation Gate)

DB role separation (blocks production / live-send) · custom-protocol CSP (blocks production
packaging) · Windows `ELECTRON_RUN_AS_NODE` handling (blocks Windows packaging). None block I1.
