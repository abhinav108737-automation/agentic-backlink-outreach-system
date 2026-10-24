# Contracts changelog

Every entry corresponds to a contract version. Changes reach this file only through the
Contract Change Request process in `docs/14_CONTRACTS_AND_PROVIDERS.md` §"Contract change process":
a lane files a CCR with the Lead, the Lead edits `contracts/`, bumps the version, adds the entry
here and regenerates the OpenAPI snapshot and the TS client. Lanes never edit `contracts/**`
or `contracts/generated_ts/**` directly (ADR-0016).

## 0.3.0 — 2026-10-25

**CCR-003** (`docs/ccr/CCR-003.md`): typed Wave 2A outreach endpoints (C8, C9). Additive —
no path removed, no `operation_id` changed, and `contracts/python/**` untouched apart from
`CONTRACT_VERSION`.

Four pre-registered `501` stubs became real operations: `approveMessage`, `sendMessage`
(returns **202** — it enqueues an `outreach.send` job and never sends inline), `getThread`,
`verifyBacklink` (also 202, enqueues `backlink.verify`).

Eight new paths, all inside existing route modules so the frozen router registry
(`routes/__init__.py`) was not edited: `/messages`, `/messages/{message_id}`, `/backlinks`,
`/backlinks/{backlink_id}`, `/campaigns/{campaign_id}/sequences`,
`/campaigns/{campaign_id}/sequences/{sequence_id}`, `/prospects/opportunities`,
`/prospects/opportunities/{opportunity_id}`.

Components: **37 → 57 schemas**. Request bodies live in `apps/api/src/kb_api/schemas/outreach.py`;
responses reuse the already-frozen C2 DTOs. The snapshot strict-parses with no NaN/Infinity
literals, so the CCR-002 rule still holds on the wire.

### Not a contract change, recorded for traceability

The outreach repositories (`sequences`, `messages`, `threads`, `replies`, `opportunities`,
`backlinks`, `provider_accounts`) were added to `kb_core.db` in this wave, taking the unit of
work from 18 to 25 repositories. `kb_core` is not a frozen contract, so this needed no CCR.

## 0.2.0 — 2026-10-18

One revision bundling **CCR-001** and **CCR-002** (`docs/ccr/`). The version bumps once, after
both were applied.

### CCR-002 — `DomainMetric.value` is nullable, and NaN sentinels are forbidden (C2, C5)

- `kb_contracts.domain.DomainMetric.value`: `float` → `float | None`.
  The database column, `SEOMetric.value` and `DomainMetricRepository.record()` were all already
  nullable; only this DTO forbade `NULL`, so a metric that was fetched and came back unavailable
  was representable everywhere except in the DTO representing a stored metric. Lane C had bridged
  the gap with a `float("nan")` sentinel, which fails safe against thresholds but cannot survive
  serialization: Pydantic emits `null` for NaN, that `null` then fails validation on the way back,
  and strict JSON refuses NaN outright. The sentinel is removed.
- `Field(allow_inf_nan=False)` added to **both** `DomainMetric.value` and `SEOMetric.value`.
  Nullability alone does not forbid a sentinel — Pydantic accepts `NaN` for a plain float field by
  default. Both DTOs are constrained because this change's whole rationale is that the two metric
  DTOs must agree; leaving one unconstrained would let the sentinel re-enter at the provider
  boundary. `None` is accepted; `NaN`/`inf` raise `ValidationError`.
- No migration: `domain_metrics.value` was already nullable in the schema and the live database.
- Guard tests: `tests/contract/test_no_nan_sentinels.py` (8 tests) — an unavailable metric
  round-trips as `None`, both DTOs reject `NaN`/`inf`, payloads survive
  `json.dumps(..., allow_nan=False)`, and the two DTOs are asserted to agree on nullability.

The three metric states this protects remain distinct end to end: a finite float (fetched with a
value), `None` (fetched but unavailable), and an absent record (never fetched). For a required
campaign metric the last two both yield `DEFERRED` and are never coerced to zero, NaN, pass or
reject (ADR-0015).

### CCR-001 — typed campaign / prospect / approvals / settings / system routes (C8, C9)

- Regenerated `contracts/openapi/openapi.json` and `contracts/generated_ts/src/api.d.ts` from the
  implemented app, replacing the untyped `501` stubs frozen at the Foundation Gate.
- Components: **2 → 37 schemas**. New paths: `/campaigns/{campaign_id}/activate`,
  `/system/kill-switch`. No path removed; existing `operation_id`s unchanged.
- Response models reuse the already-frozen C2 DTOs. API **request** bodies live in
  `apps/api/src/kb_api/schemas/`, so no new types entered the frozen contract package.
- `apps/api/src/kb_api/routes/__init__.py` (the frozen router registry) was not edited — every
  route module was pre-registered at the freeze precisely so this would not be necessary.
- Routes still intentionally `501` (other lanes): messages, threads, backlinks, reports,
  experiments, optimization proposals, providers, agent runs, and `prospects`' `research` /
  `draft-outreach` sub-routes.

### Not a contract change, recorded for traceability

An app built without persistence configured now answers implemented persistence-backed routes
with **503**, not `501`. The route exists; the dependency is absent. `501` would hide a
deployment misconfiguration behind "not implemented". `tests/security/test_api_security.py`
asserts both halves: a genuine stub still returns `501`, and `/prospects` returns `503` rather
than `200` or `501` when unconfigured.

## 0.1.0 — 2026-10-16 — FROZEN

Frozen at the Foundation Gate as implemented. This is the baseline all lanes A–F build against.

Contents: C1 enums and the 9 state-machine transition tables · C2 domain DTOs · C3 agent
input/output models · C4 per-agent tool allowlist and forbidden capabilities · C5 provider
protocols and error taxonomy · C6 policy gate with 14 block codes · C7 events · C8 HTTP API
surface (health typed; PRD §22 routes pre-registered as 501 stubs) · C9 generated TS client ·
C10 Electron bridge surface · C11 typed settings · C12 fixture formats · C13 logging and
redaction.

Changes folded in before the freeze (nothing consumed 0.1.0 yet, so the version was not bumped):
- message state machine: added the CANCELLED edges from pre-send states.
- workflow state machine: added WAITING → FAILED.
- `Settings.real_send_enabled`: now refuses real send while `autonomy_mode` is `shadow`.
- `Settings.qdrant_url`: default is the canonical service port 6333; a dev host that already
  has 6333 taken overrides `QDRANT_URL` (and `QDRANT_HOST_PORT` for the compose stack).

After this point: no change to `contracts/**` without a CCR, a version bump and an entry here.
