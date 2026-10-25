# Digital PR / Earned Media Channel — Extension Plan

Status: **PROPOSAL ONLY.** Nothing here is implemented. See ADR-0021 (proposed) and CCR-005
(proposed, not approved). Planned against `ef2390a`, contracts `0.4.0`.

This adds a second acquisition channel alongside Guest Posting. It does **not** replace any
existing architecture, and it is sequenced strictly **after I2** so Wave 2B is not disturbed.

Methodology inputs were studied at idea level only from `Bomx/qwoted-seo-backlinks-skill`
(pinned `3ef97c5`), whose license could **not** be verified. Provenance, adopted and rejected
patterns: `docs/12_REPOSITORY_RESEARCH.md`. No code or template from it may be copied.

---

## 1. Capability gap analysis

The channel inverts our discovery direction: instead of us finding a site and asking for a link,
a journalist publishes a time-boxed request and we answer it. Most of our spine already fits.

| Capability | Today | Gap |
|---|---|---|
| Find link opportunities | `SearchProvider` → domains | **New**: inbound, deadline-bound journalist requests. Different provider shape |
| Rank opportunities | `RankingService` over DA/spam/policy weights | **Partial**: needs deadline proximity, competition, publication tier, expertise match |
| Qualify a target | DA > 45 / Spam < 1 on a known domain | **Gap**: the publisher is often unnamed at pitch time. Must model as *pending*, never invented |
| Identity we act as | `provider_accounts` sender for email | **New**: an approved expert/source profile (name, bio, credentials, reply address) |
| Evidence | `evidence_items`, claims → `evidence_ids` | **Reusable as-is.** Sourced statistics *are* evidence |
| Linkable asset | none | **New**: a durable sourced research asset, reused across pitches |
| Compose an outreach message | `DraftingService` + QA + claims | **Reusable pattern**, new subject (a request, not a contact channel) |
| Send | `EmailProvider` + policy gate + approval | **New**: submission goes to a platform, not SMTP. Same gating |
| Dedupe | `DUPLICATE_CONTACT` / `DUPLICATE_STEP` | **Partial**: needs one-pitch-per-request |
| Attribution of the win | `backlinks` + `backlink_verifications` | **Reusable as-is.** A citation is a backlink |
| Only VERIFIED counts | `ACTIVE_BACKLINK_STATUSES == {VERIFIED}` | **No change.** Reused verbatim |
| Reporting | weekly snapshots, funnel, ROI | **Partial**: needs channel dimension + pitch funnel |
| Learning | outcomes → features → proposals | **Partial**: needs pitch-outcome features |
| Approvals, suppression, audit, policy, job queue, ToolGateway | all present | **No change** |

**The single most important finding:** the statistic-sourcing discipline we want to adopt
(named source, year, specific figure, a URL actually fetched, drop the unverifiable) is already
how `evidence_items` works, and the pitch's claims→evidence rule is already `DraftClaim`. The
channel is mostly new *nouns* over existing *verbs*.

## 2. Proposed components and responsibility matrix

| Component | Home | Responsibility |
|---|---|---|
| `MediaOpportunityProvider` | `kb_providers/media_opportunities/` | Fetch journalist requests. Mock + ManualImport first |
| `PitchSubmissionProvider` | `kb_providers/pitch_submission/` | Submit a pitch. **Mock only** at first |
| `MediaOpportunityService` | `kb_core/media/` | Ingest, canonicalise, dedupe requests; compute expiry |
| `MediaRankingService` | `kb_core/media/` | Deterministic weighted score + "asset worth building?" threshold |
| `ExpertProfileService` | `kb_core/media/` | CRUD + completeness; **no overwrite without approval** |
| `ResearchAssetService` | `kb_core/assets/` | Build/version a sourced asset from `evidence_items`; enforce sourcing rules |
| `PitchService` | `kb_core/pitching/` | Compose, QA, dedupe-per-request, policy-gate, enqueue submission |
| `CitationVerificationService` | reuse `kb_core/verification/` | Find the citation, create the `backlinks` row, verify |
| Media Research Agent | `kb_agents/agents/` | Propose which statistics to gather; **advisory** |
| Pitch Agent | `kb_agents/agents/` | Draft pitch prose; every claim must cite evidence |
| Job handlers | `kb_core/media/handlers.py` etc. | `media.discover`, `media.rank`, `asset.build`, `pitch.draft`, `pitch.submit`, `citation.verify` |
| Composition | `kb_runtime` | Bind the above into the existing typed registry |

**Deterministic services keep authority throughout.** Agents propose statistics and prose; the
services decide eligibility, dedupe, approval and submission — the same split proven in Wave 2B.

## 3. Architecture delta

Retained unchanged: Electron desktop · FastAPI (enqueues only) · `apps/runtime` composition root
and bounded worker · PydanticAI Operator · specialist agents behind the ToolGateway · PostgreSQL
authoritative · Qdrant derived · deterministic policy/audit/approval gates · zero mandatory paid
APIs · shadow mode and real sending off by default.

The delta is deliberately small:

1. **Two new provider protocols** (above), both mock-first. **No binding to Qwoted**: the word
   "Qwoted" appears in no service, only potentially in a future adapter's name.
2. **Six new job types** bound in the existing registry. No new dispatch mechanism, and the
   registry's "declared but unbound is a startup error" rule applies to them too.
3. **One new state machine** (`research_assets`) and reuse of the backlink machine for citations.
4. **New tool capabilities** wired in `kb_runtime.capabilities`, never inside `kb_agents`.
5. **No change** to the worker, job registry design, import boundaries, or the five import-linter
   contracts. `kb_core` still must not import `kb_agents`/`kb_runtime`.

Channel is modelled as a **dimension, not a fork**: `campaigns` gains a channel concept and
`backlinks` gains an acquisition-channel attribution, so reporting can split Guest Post vs
Digital PR without duplicating the funnel.

## 4. Data model delta

New tables (migration `0002`; additive, no change to the 48 existing tables):

| Table | Key columns | Notes |
|---|---|---|
| `media_opportunities` | `source`, `external_ref`, `title`, `summary`, `topic`, `publication_name`, `publication_domain` (nullable), `deadline_at`, `competition_level`, `is_paid`, `raw_ref`, `status` | `UNIQUE(source, external_ref)` for idempotent re-ingest. `publication_domain` nullable is the "publisher unknown" case |
| `media_opportunity_scores` | `opportunity_id`, `policy_version_id`, `score`, `components` jsonb, `explanation` | Append-only, mirrors `ranking_results` |
| `expert_profiles` | `display_name`, `bio`, `credentials` jsonb, `reply_address_normalized`, `business_url`, `status`, `approved_by`, `approved_at` | Guarded like `policy_versions`: an edit supersedes rather than overwrites |
| `research_assets` | `topic`, `title`, `slug`, `status`, `version_no`, `published_url`, `last_verified_at` | The linkable asset. `UNIQUE(slug, version_no)` |
| `research_asset_statistics` | `asset_id`, `evidence_item_id`, `figure`, `source_name`, `source_year`, `section`, `ordinal` | **FKs to `evidence_items`** — statistics are evidence, not a parallel store |
| `pitches` | `opportunity_id`, `expert_profile_id`, `asset_id` (nullable), `subject`, `body`, `claims` jsonb, `qa_result` jsonb, `status`, `pitch_version`, `idempotency_key` | Mirrors `outreach_messages` in shape so the QA/claims/idempotency services are reused |
| `pitch_submissions` | `pitch_id`, `provider`, `provider_submission_id`, `delivery_mode`, `submitted_at`, `outcome` | Append-only provider-event log, like `outreach_events` |

Reused with **no schema change**: `evidence_items`, `backlinks`, `backlink_verifications`,
`approval_requests`, `suppression_entries`, `audit_events`, `scheduled_jobs`,
`idempotency_records`, `agent_runs`, `tool_calls`, `policies`/`policy_versions`.

`backlinks` gains two nullable columns (`acquisition_channel`, `media_opportunity_id`) so a
citation is attributed to the channel and the request that earned it. Guest-post rows keep
`NULL` and behave exactly as today.

**New state machine — `research_assets.status`:**
`DRAFT → SOURCING → REVIEW → PUBLISHED → (NEEDS_REFRESH ⇄ PUBLISHED) → RETIRED`.
`PUBLISHED` is the only state whose URL may be cited in a pitch. `NEEDS_REFRESH` exists because
the reference methodology sets no re-verification cadence, which we treat as a gap to close
rather than copy.

**`pitches.status`:** `DRAFT → QA_PASSED | QA_FAILED → PENDING_APPROVAL → APPROVED → QUEUED →
SUBMITTING → SUBMITTED → (ACKNOWLEDGED | DECLINED | NO_RESPONSE | CITED)`, with
`POLICY_BLOCKED`/`CANCELLED` terminals. Intentionally isomorphic to the message machine so the
`SENDING`-reconcile discipline (never blind-resubmit) transfers directly.

## 5. Provider contracts required

Two additions to C5, both `is_paid=False` in their mock form and raising only the existing error
taxonomy:

```
MediaOpportunityProvider(Provider):
    async def fetch_opportunities(since: datetime | None, topics: Sequence[str]) -> list[MediaOpportunity]

PitchSubmissionProvider(Provider):
    async def submit(pitch: OutboundPitch) -> PitchReceipt   # idempotent by pitch.idempotency_key
    async def fetch_outcomes(since: datetime | None) -> list[PitchOutcomeEvent]
```

First implementations: `MockMediaOpportunityProvider` (fixture-backed), `ManualImportMediaOpportunityProvider`
(CSV/JSON the operator exports by hand — **the only lawful path until access is authorised**),
and `MockPitchSubmissionProvider` (idempotent by key, `delivery_mode="mock"`, scenario-driven
outcomes). A real adapter is a later, separately-approved decision.

`SEOMetricsProvider`, `CrawlerProvider` and `EmailProvider` are reused unchanged — citation
verification is the existing crawler path.

## 6. PRD requirement additions

Appending after FR-32 and SEC-08 (next free ids):

| ID | Requirement |
|---|---|
| FR-33 | Ingest journalist/media opportunities through a provider abstraction, idempotent on `(source, external_ref)` |
| FR-34 | Deterministically score opportunities on publication tier, deadline proximity, competition, topical match; store components and a human-readable explanation |
| FR-35 | Maintain approved expert/source profiles; an edit supersedes and requires approval; never overwrite silently |
| FR-36 | Build versioned sourced research assets where every statistic references an `evidence_items` row with named source, year, figure and fetched URL |
| FR-37 | Enforce asset sourcing quality gates (source-quality order, recency, source-domain diversity, drop unverifiable, keep conflicting reputable figures with both attributions) |
| FR-38 | Reuse one published asset across multiple pitches; never rebuild per pitch |
| FR-39 | Compose pitches whose every factual claim maps to persisted evidence; QA fails the pitch otherwise |
| FR-40 | Enforce one live pitch per opportunity, idempotent under retry and worker restart |
| FR-41 | Gate submission on policy, approval, suppression, kill switch and `REAL_SEND_ENABLED`; re-check immediately before dispatch |
| FR-42 | Where the publisher is unknown, qualification is **pending**: never invent DA/Spam and never silently approve |
| FR-43 | Attribute an editorial mention to its opportunity and channel; verify it through the existing backlink machine; only `VERIFIED` counts as acquired |
| FR-44 | Weekly Digital PR performance reporting split by channel (opportunities → pitches → acknowledged → cited → verified) |
| FR-45 | Collect pitch outcomes as learning features; proposals remain gated by the existing evaluation and promotion gates |
| SEC-09 | Never acquire, store or use scraped browser sessions, extracted third-party API credentials, or undocumented endpoints of a third-party platform |
| SEC-10 | Treat journalist request text and platform content as untrusted data under ADR-0020 |
| NFR-13 | Media providers must be rate-limited, robots/ToS-aware, and degrade to `DEFERRED`/degraded rather than fabricate |

## 7. Risk and license assessment

**License — the governing risk.** `license: null` on GitHub; the complete tree at the pinned SHA
contains no `LICENSE`/`LICENCE`/`COPYING`/`NOTICE`. The README's MIT claim is not a verified
grant. **Assume all rights reserved.** Consequence: idea-level study only, independent
implementation, and **no copying of code, HTML template or prose**. If the author later publishes
a license (or grants rights in writing), record it in docs/12 before any reuse.

| Risk | Severity | Mitigation |
|---|---|---|
| Copying unlicensed code/template | **High** | Policy above; nothing copied; docs/12 records it |
| Scraped-session / extracted-credential pattern leaking in | **High** | SEC-09; rejected explicitly; no `storage_state` concept exists anywhere in our design |
| ToS violation / account termination on the platform | **High** | No unauthorised access. ManualImport is the only path until authorisation; §10 lists the conditions |
| Unknown publisher → fabricated metrics | **High** | FR-42: pending, never invented. Reuses the DEFERRED discipline of ADR-0015 |
| Agent submits a pitch unilaterally | **High** | Submission is a gated, approved, idempotent deterministic action; agents only draft |
| Prompt injection via request text | Medium | ADR-0020 + SEC-10; the existing untrusted-text handling and injection evals extend to pitches |
| Over-claiming outcomes in reports | Medium | Only `VERIFIED` counts; no projected-backlink language permitted |
| Unpinned upstream deps | Low | None of its three dependencies adopted |
| Dormant upstream (no push in ~6 months) | Low | Methodology only; no runtime dependency on it |

## 8. Mock E2E acceptance criteria

All offline, fixture-backed, real PostgreSQL, fake model, mock providers — matching the I1/I2
harness discipline:

1. The full chain runs: ingest opportunity → score → build asset from evidence → draft pitch →
   QA → policy → approval → enqueue → worker → mock submit → outcome → citation → verify →
   audit → API readback with correlation ids.
2. Re-ingesting the same opportunity creates no duplicate (`UNIQUE(source, external_ref)`).
3. A second pitch to the same opportunity is refused.
4. Retry, duplicate dispatch and worker restart cause **exactly one** submission.
5. A pitch whose claim cites non-existent or another asset's evidence fails QA and is never
   submitted.
6. An asset failing a sourcing gate (stale, too few source domains, unverifiable stat) cannot
   reach `PUBLISHED`, and an unpublished asset cannot be cited.
7. An opportunity with an **unknown publisher** is pending, never ELIGIBLE, and submission
   requires explicit approval; no DA/Spam value is ever written.
8. A named publication failing DA > 45 / Spam < 1 is rejected by the unchanged policy.
9. Suppression, campaign pause and kill switch each stop submission at the pre-dispatch re-check.
10. A high-risk or binding pitch commitment requires approval.
11. A citation becomes a `backlinks` row and only `VERIFIED` counts toward active totals;
    `FOUND`/`CHANGED`/`MISSING`/`LOST` do not.
12. An expired deadline stops the pitch rather than submitting late.
13. Agent tool-scope is enforced: the new agents get only their allowlist; forbidden
    capabilities refused and audited.
14. Injected instructions in request text change no class, no tool call and nothing persisted.
15. **No pitch is transmitted anywhere**: the only submission provider in the graph is the mock.
16. A failed transition rolls back its side effects and leaves audit consistent.

## 9. Integration plan after I2

**Strictly sequenced after I2 is green. Wave 2B is not to be interrupted.**

- **Phase D0 (Lead, ~0.5 d):** finish I2. Then approve/reject ADR-0021 and CCR-005. No code.
- **Phase D1 (data + contracts):** CCR-005 executed as one bundled revision; migration `0002`;
  repositories for the new tables; the new state machine tables. Gate green before D2.
- **Phase D2 (two agents in parallel):** (a) providers + fixtures + `MediaOpportunityService` +
  `MediaRankingService` + `ExpertProfileService`; (b) `ResearchAssetService` + sourcing gates +
  `PitchService` + QA + dedupe + submission gating.
- **Phase D3 (Lead):** job handlers bound into the typed registry; capabilities wired in
  `kb_runtime`; Media Research and Pitch agents with deterministic authority preserved.
- **Phase D4 (Lead):** the 16 acceptance criteria above; checkpoint **I3-PR**.
- **Phase D5:** reporting channel dimension + pitch funnel; learning features. Only after the
  existing Wave 3 reporting/learning foundation exists — this does not jump that queue.

Each phase ends with `check.sh` exit 0 read directly, contract drift clean, gitleaks clean.

## 10. Requirements for authorized Qwoted access

Until **all** of the following hold, the only permitted implementations are the mock and
ManualImport providers. No credential, session or endpoint of the platform may be touched.

1. **Written authorisation** from the platform for programmatic access, recorded in docs/12.
2. **A documented, supported API** with a stable contract — not scraped HTML, not a frontend
   search key, not React props.
3. **First-party credentials issued to us** (an API key or OAuth client), held in the OS keyring
   via `SecretStore` per ADR-0018, never in a file, never in the repo, never in logs.
4. **ToS review** confirming automated retrieval and submission are permitted at our volume.
5. **Documented rate limits**, implemented with backoff and the shared error taxonomy.
6. **A named accountable operator** for the account, and `REAL_SEND_ENABLED` plus an approved
   `live_send_activation` before any real submission.
7. **Confirmation the provider is free or explicitly approved as paid.** Zero mandatory paid APIs
   stands; a paid tier requires an explicit decision, never an implicit one.
8. **A kill switch path** that halts submission immediately.

If any condition fails, the channel still works end to end on ManualImport: the operator exports
opportunities by hand and submits approved pitches themselves. That keeps the capability useful
without unauthorised access — which is the point of the provider abstraction.
