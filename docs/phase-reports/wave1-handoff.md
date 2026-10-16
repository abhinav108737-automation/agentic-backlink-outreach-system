# Session Handoff — Wave 1 (toward integration checkpoint I1)

Date 2026-10-16 · branch `foundation/g0-gate` · last commit `fc424ab` · tree clean
Baseline: `baa5a7e` (canonical docs + ADRs) → `84af80f` (Foundation Gate accepted) → `fc424ab` (this partial Wave 1).

## Status: I1 NOT reached

Wave 1 was interrupted. All three implementation agents (Lanes B, C, E) terminated on an
**account session rate limit** (resets 21:20 Asia/Calcutta) before completing their bounded
tasks. No lane finished. Lane B produced nothing. The partial work that survived is committed
and green, but the I1 slice does not run end to end and **no I1 checkpoint commit was made**.

This was an infrastructure interruption, not a design or integration failure. Nothing is known
to be wrong with the plan; it simply did not finish.

## Verified green on commit `fc424ab`

| Gate | Result |
|---|---|
| ruff check / format | clean, 120 files |
| mypy strict | no issues, 50 source files |
| import-linter | 3/3 contracts kept |
| pytest | 123 passed (unchanged from Foundation — no new tests were written) |
| manual smoke | MockSearchProvider + MockCrawlerProvider exercised against fixtures by hand |

The 123 figure is the Foundation baseline. **The new code in this commit has no automated test
coverage of its own** — every lane died before writing tests. Treat the partial code as
unverified beyond lint, types and a hand smoke.

## What exists now

**Lead**
- `services/core/src/kb_core/db/protocols.py` — repository + `UnitOfWork` seam, **frozen for
  Wave 1**. Written so Lanes B and C could run in parallel instead of C-then-B. Key invariant
  encoded in it: `DomainMetricRepository.latest` keeps *never fetched* (key absent) distinct
  from *fetched but unavailable* (value `None`). Collapsing those would let a prospect pass
  qualification on a missing metric, which ADR-0015 forbids — both must DEFER.
- `docs/ccr/CCR-001.md` — filed and approved, **not executed**.

**Lane C (partial)** — `kb_core/db/tables.py` (SQLAlchemy Core metadata over migration 001),
`engine.py` (async engine factory), `page.py` (`CursorPage`).
Outstanding: every repository implementation, `SqlUnitOfWork`, seed policy v1, Qdrant
collections + rebuild command, and all DB tests (trigger enforcement, optimistic-lock conflict,
idempotency race, `FOR UPDATE SKIP LOCKED` job race, metric absent-vs-None, UoW rollback).

**Lane E (partial)** — 21-site fixture corpus in the frozen C12 formats, 4 SERP fixtures,
2 metrics CSVs, `MockSearchProvider`, `MockCrawlerProvider`, `html_to_text`.
Label coverage is complete across 21 sites: eligible 11, rejected 4, spammy 3,
missing_metrics 3, prompt_injection 3. All three prompt_injection sites are also labelled
eligible, so an injected page cannot be skipped merely because it would fail qualification —
the injection test runs on pages that would otherwise be used.
Outstanding: `MockSEOMetricsProvider`, `ManualImportSEOMetricsProvider`, canonicalization +
dedupe, deterministic research extraction, deterministic contact extraction, and all tests
including the mandatory prompt-injection inertness test (ADR-0020).

**Lane B** — nothing landed.
**Lane D** — never launched (it was batch 2 by design, since ToolGateway depends on B and C).

## The DEFERRED case I1 requires is already fixture-ready

`fixtures/metrics/baseline.csv` encodes three distinct *fetched-but-unavailable* shapes:

| Domain | domain_authority | spam_score | Why it must DEFER |
|---|---|---|---|
| `emergingmarketspulse.io` | 61.0 (would pass) | **blank** | one required metric unavailable |
| `newtradejournal.com` | **blank** | 0.3 | the other one unavailable |
| `quietdataweekly.net` | **blank** | **blank** | both unavailable |

A fourth shape — *never fetched* — is produced by any domain absent from the CSV entirely.
Qualification must DEFER on all four and must never pass any of them.

## Resume instructions (in order)

Do **not** start fresh agents for Lanes B, C and E — their partial work and context are saved.
Resume each with `SendMessage` to its existing agent so it continues from where it stopped:

- Lane E — agent `a01686e93c1e33ea2` (stopped after fixtures, mid provider work)
- Lane C — agent `a2e04c21d2ec568c8` (stopped after table metadata)
- Lane B — agent `aa6b3a1744ac9ca8f` (stopped before writing anything)

1. Confirm the rate limit has reset, then resume the three agents. Respect the cap of 3 concurrent
   implementation agents and 1 child each.
2. Integrate: run `infrastructure/scripts/check.sh` (compose stack must be up; `.env` supplies
   `QDRANT_HOST_PORT=56333` on this machine).
3. Launch Lane D (batch 2) once B and C are green.
4. Execute CCR-001: `CONTRACT_VERSION` → `0.2.0`,
   `uv run python -m kb_api.export_openapi`, `npm run gen:contracts`,
   `infrastructure/scripts/check-contracts.sh`, `contracts/CHANGELOG.md` entry.
   The contract drift gate **will fail** between Lane B landing handlers and this step — that is
   expected and is the Lead's job to close, not a lane's.
5. Wire and prove the I1 slice: discover → qualify (**including a DEFERRED prospect**) →
   research → contact → exposed through the API.
6. Only then make the I1 checkpoint commit, and stop before Wave 2.

## Open risks

1. **No test coverage on the partial code.** It passes lint and types and nothing more. Lanes must
   write their tests on resume; do not treat the green gate as validation of this commit's new code.
2. **CI is still externally unverified** (no Git remote, per Foundation decision 4). Every "green"
   in this handoff means green on this machine only.
3. **Rate-limit exposure.** Three concurrent Opus agents exhausted the session budget quickly.
   Consider resuming them in sequence, or two at a time, rather than three at once.
4. The frozen seam has not yet been exercised by a real implementation on both sides, so a
   signature problem would surface on resume. If one does, it comes to the Lead — Lane B and C
   must not edit `protocols.py` independently.

## Unchanged release blockers (from the Foundation Gate)

DB role separation (blocks production / live-send) · custom-protocol CSP (blocks production
packaging) · Windows `ELECTRON_RUN_AS_NODE` handling (blocks Windows packaging). None of these
block I1.
