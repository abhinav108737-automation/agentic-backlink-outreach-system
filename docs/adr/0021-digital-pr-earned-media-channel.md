# ADR-0021: Digital PR / Earned Media as a second acquisition channel

Status: **Proposed.** Date: 2026-10-25. Supersedes nothing. Decided by: pending user approval.

## Context

Guest Posting is our only acquisition channel: we discover a site, qualify it on DA/Spam, research
it, find a contact and pitch a post. It is outbound and we choose the target.

Journalist-request platforms invert that. A reporter publishes a time-boxed request for an expert
source; answering it well earns an editorial mention and a citation from a publication we could
not have pitched cold. The mechanics differ from guest posting in four ways that matter
architecturally: the opportunity is **inbound and expires**; we act as a named **expert source**
rather than a content supplier; the durable artefact is a **reusable sourced research asset**
rather than one article per site; and **the publisher is frequently unnamed** at pitch time.

We studied `Bomx/qwoted-seo-backlinks-skill` (pinned `3ef97c52`) for methodology. Its license
could not be verified — GitHub reports `license: null` and the complete tree at that SHA contains
no license file — so it was an idea-level study only, with nothing copied
(`docs/12_REPOSITORY_RESEARCH.md`). Several of its implementation choices are directly contrary
to our rules: scraped browser sessions on disk, an Algolia search key extracted from page React
props with origin-spoofed requests, undocumented endpoints, and JSON files as authoritative
state.

## Decision

Add Digital PR as a **second channel on the existing spine**, not a parallel system.

1. **Channel is a dimension, not a fork.** Campaigns carry a channel, and `backlinks` gains
   nullable channel/opportunity attribution. Guest-post rows are unaffected. Reporting splits by
   channel instead of duplicating the funnel.
2. **Reuse the evidence spine verbatim.** A sourced statistic *is* an `evidence_items` row, and a
   pitch's claims use the existing claims→`evidence_ids` rule. This is the main reason the
   channel is cheap to add: the sourcing discipline we want was already our data model.
3. **Reuse the backlink machine verbatim.** An editorial citation is a `backlinks` row moving
   `EXPECTED → FOUND → VERIFIED | CHANGED | MISSING`, and
   `ACTIVE_BACKLINK_STATUSES == {VERIFIED}` is unchanged. Only `VERIFIED` counts as acquired.
4. **Two new provider protocols**, mock-first: `MediaOpportunityProvider` and
   `PitchSubmissionProvider`. **No service binds to Qwoted**; a real adapter is a separate,
   separately-authorised decision (docs/15 §10).
5. **Unknown publisher ⇒ qualification pending.** Never invent DA or Spam Score, never silently
   approve. This extends ADR-0015's "missing metric ⇒ DEFERRED, never passed" to "unknown
   publisher ⇒ pending, never passed". The DA > 45 / Spam < 1 rule is untouched where the
   publisher *is* known.
6. **Deterministic services keep authority.** Agents propose which statistics to gather and draft
   pitch prose. Eligibility, dedupe, approval, submission and state transitions stay
   deterministic, exactly as Wave 2B established for replies and negotiation.
7. **Submission is a consequential outward action.** It goes through the policy gate, an
   approval, idempotency and the pre-dispatch re-check, and is impossible while
   `REAL_SEND_ENABLED=false`. The first implementation submits to a mock only.
8. **Explicitly rejected** (see docs/12 for the full table): scraped browser-session cookies,
   extracted third-party API credentials, undocumented platform endpoints, file-based
   authoritative state, unrestricted agent submission, projected-backlink claims, and the
   reference's mandatory zero-outbound-links rule on research assets.

On that last point: the reference strips all external links from the asset on link-equity
grounds, while simultaneously arguing that transparent sourcing protects credibility. Those are
in tension, and a plain-text attribution a reader cannot follow is *less* verifiable. Our whole
evidence model exists to make claims checkable, so source links stay and outbound-link policy is
a configurable editorial choice rather than a mandate.

## Consequences

**Positive.** A second channel reusing the policy, approval, audit, idempotency, job-queue,
verification and agent-containment machinery already built and tested. Research assets are
reusable across pitches, so cost per pitch falls over time. The provider abstraction means the
channel is useful via ManualImport with no platform access at all.

**Negative / accepted costs.** One new state machine and seven new tables (migration `0002`,
additive). A bundled CCR (CCR-005) touching C1, C2, C4, C5, C6 and C8/C9 — a larger contract
revision than any so far, which is why it is enumerated and approved as one unit rather than
drifting in piecemeal. A pitch funnel that reporting and learning must learn to split by channel.

**Risks accepted.** Without platform authorisation the channel runs on manual export/submit,
which is lower throughput; we prefer that to unauthorised access. Citation attribution is
probabilistic — a mention may appear without a link, or with a link we cannot attribute to a
specific pitch; we record what we can verify and count only `VERIFIED`.

## Alternatives considered

- **A separate Digital PR application.** Rejected: it would duplicate policy, audit, approval and
  verification, and the two channels need one funnel and one backlink total.
- **Model a journalist request as a `prospect`.** Rejected: prospects are domains with metrics and
  a lifecycle built around contacting a site. A request has a deadline, competition and often no
  domain at all; forcing it in would distort the prospect machine we already rely on.
- **Reuse `outreach_messages` for pitches.** Rejected: it is anchored to a sequence and a contact
  channel, neither of which a pitch has. We reuse the *services* (QA, claims, idempotency,
  pre-dispatch re-check) over a `pitches` table of the same shape instead.
- **Bind directly to Qwoted now.** Rejected on license, ToS and authorisation grounds, and it
  would violate the provider-replaceability principle in CLAUDE.md.

## Compliance with existing decisions

Honours ADR-0003 (Qdrant derived), ADR-0005 (deterministic enforcement outside the model),
ADR-0007 (approval gates), ADR-0008/0017 (PostgreSQL job queue), ADR-0015 (never pass on missing
data — extended here), ADR-0016 (contract-first; CCR-005 required), ADR-0018 (secrets in the
keyring, never files), ADR-0020 (request text is untrusted data). Zero mandatory paid APIs and
shadow-by-default are preserved.
