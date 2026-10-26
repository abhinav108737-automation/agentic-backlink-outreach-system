# Backlink Automation — Measurable Pilot Plan

Status: **proposal for review.** Written for Avdhesh (accepting reviewer) and Shrey (joint
delivery), 2026-10-26, in response to the 19–24 October review.
System state it is based on: commit `6b8f422`, contracts `0.4.0`.

Fields marked **[TBD — needs your input]** are ones I cannot answer alone and have not guessed.

---

## 1. What already exists (so the pilot is not starting from zero)

The review asks for a smallest safe first version with human approval, a duplicate check, an
opt-out/suppression check and a failure queue. All four are built and under test today, with the
defaults set to fail closed:

| Review requirement | Status | Where |
|---|---|---|
| Human approval before outreach | **Built.** `require_approval_for_send = True` by default; a message must pass `PENDING_APPROVAL → APPROVED` with a recorded decider before it can queue | `approval_requests`, message state machine |
| Duplicate check | **Built.** Two policy gates (`DUPLICATE_CONTACT`, `DUPLICATE_STEP`) plus database constraints (`one_live_message_per_step`, unique per sequence/step/version) and an idempotency key per message | policy engine + schema |
| Opt-out / suppression check | **Built.** `SUPPRESSED` blocks at enqueue *and again immediately before dispatch*; opt-out and bounce replies create a suppression entry in the same transaction; suppression rows cannot be deleted, only superseded | `suppression_entries` |
| Failure queue | **Built.** Jobs retry with bounded backoff then dead-letter to `DEAD` for human review; blocked messages go to `POLICY_BLOCKED`; failed content QA goes to `QA_FAILED` | `scheduled_jobs` |
| Relevance and editorial fit recorded, not just DA/Spam | **Built.** Recorded as separate scored fields alongside the hard screen, with the ranking components and a human-readable explanation stored per prospect | `qualification_decisions`, `ranking_results` |
| A sent message must not count as a backlink | **Built and enforced in one place.** Only `VERIFIED` counts as an acquired link; `EXPECTED`, `FOUND`, `CHANGED`, `MISSING` and `LOST` all count as zero | `ACTIVE_BACKLINK_STATUSES` |
| Real sending off by default | **Built.** `REAL_SEND_ENABLED=false`, `AUTONOMY_MODE=shadow`; enabling real send additionally requires an approved activation record | config + policy |

Ten other deterministic gates are also live (campaign paused, kill switch, send limits, missing
metric, hard threshold, unsupported claim, invalid sender, and others).

## 2. What is NOT ready — the two things that constrain pilot timing

I would rather say this plainly than plan around it.

1. **There is no real email sender yet.** The only implemented email provider is a mock. A live
   send is currently impossible, not merely disabled. Building and credentialing a real sender
   (Gmail OAuth or IMAP/SMTP) is a prerequisite for any stage that sends.
2. **There is no automated source for Domain Authority / Spam Score.** The implemented providers
   are a fixture mock and a manual CSV import. See §5 — this is also the honest answer to the
   paid-API question.

Also relevant to scheduling: the current integration checkpoint (I2) has **2 of 14 acceptance
tests written**, and a database role-separation hardening item is recorded as a blocker on
live-send release. Stage C below is gated on both.

## 3. The pilot, in three stages with explicit acceptance criteria

Each stage has a go/no-go. No stage starts before the previous one passes.

### Stage A — Dry run on fixtures (no sending)

| | |
|---|---|
| Purpose | Prove the gates fire and the funnel numbers reconcile, before any real data or contact |
| Size | 150 fixture prospects, including deliberately ineligible, missing-metric, spammy and opt-out cases |
| Window | 2 working days |
| Sends | **Zero**, structurally impossible |
| Owner | Abhinav (delivery) |
| Reviewer | Avdhesh |

**Acceptance criteria (all must hold):**
- Every funnel stage count reconciles: discovered = qualified + rejected + deferred, and so on
  down to verified. No stage silently loses or invents a record.
- A prospect missing DA or Spam Score ends `DEFERRED` and cannot be sent to.
- A prospect failing DA > 45 or Spam < 1 ends `REJECTED`.
- A suppressed address is never dispatched to, including when the suppression is added *after*
  approval.
- A duplicate contact and a duplicate step are both refused.
- A deliberately broken job lands in the failure queue and is visible for review.
- Zero emails transmitted, evidenced by the provider's own delivery log.

### Stage B — Shadow run on real data (no sending)

| | |
|---|---|
| Purpose | Test real discovery and real screening quality with a human reviewing every decision |
| Size | ≥ 100 real candidate domains screened |
| Window | 1 week |
| Sends | **Zero** (shadow mode) |
| Owner | Abhinav + Shrey |
| Reviewer | Avdhesh |

**Acceptance criteria:**
- ≥ 100 domains screened with DA/Spam recorded from a real source (manual CSV import is
  acceptable here).
- A human reviews the qualified set and records agreement or disagreement per prospect. **This is
  the measurement that tells us whether DA/Spam plus relevance and editorial fit is actually a
  good screen** — it is the main thing Stage B exists to learn.
- Relevance and editorial fit are recorded for every qualified prospect, with the ranking
  explanation legible to a reviewer who did not build the system.
- Drafts are generated and reviewed for quality, but nothing is approved for sending.
- Every factual claim in a draft traces to stored evidence; a draft with an unsupported claim
  fails QA rather than being sent.

**Go/no-go for Stage C:** the reviewer agrees with the qualification decision on **≥ 80%** of
qualified prospects, and draft quality is judged acceptable to send.

### Stage C — First live sends, capped

| | |
|---|---|
| Purpose | Get real reply and link data at the smallest size that can produce a signal |
| Size | **≤ 150 approved sends total**, ≤ 25/day, ≤ 1 per domain, ≤ 2 follow-ups per prospect |
| Window | 3 weeks (≈ 2 weeks sending + 1 week for replies and publication to land) |
| Approval | **100% human-approved.** No autonomous sending in the pilot |
| Owner | Abhinav + Shrey |
| Reviewer | Avdhesh |

**Prerequisites — Stage C cannot start until all are true:**
1. A real email sender is implemented and credentialed, with a named sending identity.
2. The database role-separation blocker is closed (it currently gates live-send release).
3. The I2 acceptance suite is complete and green.
4. `REAL_SEND_ENABLED=true` with an approved activation record and a working kill switch.

**Acceptance criteria — these are process criteria, not outcome targets:**
- 100% of sent messages have a recorded human approver.
- Zero sends to a suppressed or opted-out address.
- Zero duplicate sends to the same domain or contact.
- Every reply classified, with opt-outs and bounces auto-suppressing.
- Every claimed link verified before it is counted; only `VERIFIED` appears in the total.
- A complete audit trail exists for every sent message, attributable to an approver and a policy
  version.
- Kill switch halts sending immediately when exercised (tested once, deliberately).

## 4. The four funnel measures, defined so they cannot be conflated

Reported separately every week. A generated prospect or a sent message is never a backlink.

| Measure | Definition | Counted when |
|---|---|---|
| **Qualified prospects** | Passed DA > 45 **and** Spam < 1 **and** has recorded relevance and editorial fit | Qualification decision is `ELIGIBLE` |
| **Approved outreach** | A human approved a specific message for a specific prospect | Message reaches `APPROVED` with a recorded approver |
| **Replies** | An inbound human reply, excluding bounces and auto-responders | Reply classified as positive/question/negotiation/negative |
| **Verified live links** | A link to our target, on the agreed page, found by automated verification | Backlink status is `VERIFIED` |

Also reported, because they are the honest denominators: discovered, deferred (missing metrics),
rejected, blocked by policy, in the failure queue, and sends that produced no reply.

**A sample-size caution I want on the record.** Cold outreach reply rates are typically
single-digit to low-double-digit percent, and only a fraction of replies become published links.
At 150 sends, the arithmetically likely outcome is **0–3 verified links**. That is a function of
sample size, not of whether the system works. Stage C should be judged on the process criteria in
§3 and on the funnel conversion *rates* it measures — not on hitting a link count. If a link
count is wanted as the headline, the pilot needs to be materially larger, and I would rather
agree that trade-off up front than report against a bar the sample cannot clear.

## 5. The zero-paid-API assumption, validated

**It holds for everything except one input.** Discovery, crawling, research, drafting, reply
handling, verification, reporting and the local AI model all run with no paid API — the model
runs locally via Ollama, and search and crawling use self-hostable or free components.

**The exception is authoritative Domain Authority and Spam Score.** These are proprietary Moz
metrics. There is no zero-cost legal source: the options are a paid API, or an existing
subscription exported by hand. We recorded this as an open provider question rather than assume
it away. Three options:

| Option | Incremental cost | Trade-off |
|---|---|---|
| **A. Manual CSV import** from an existing Moz/Ahrefs/SEMrush subscription | **Zero incremental** | Manual effort per batch; fine at pilot size, does not scale |
| **B. Paid metrics API** | Needs a quote and an explicit decision | Scales; breaks the zero-paid principle, so it must be an explicit choice, never a silent default |
| **C. Free proxy metrics** (e.g. open link-graph sources) | Zero | Not DA/Spam. Changes the screening rule, so it is a policy decision, not a swap |

**Recommendation for the pilot: Option A.** It keeps incremental cost at zero and is sufficient
for Stages A–C. The scaling decision can wait for pilot operating data.

**On cost and savings:** the model stays explicitly labelled an **estimate** until Stage C
produces real operating data — time per approved send, reply rate, verified-link conversion. I
will not restate assumptions as achieved savings.

## 6. The 5,000-lead ambition, decomposed

The figure needs a unit, a period and a measurement before it can be a target. Decomposed against
the system's own limits:

| Reading of "5,000" | Feasible? | Period implied |
|---|---|---|
| 5,000 **candidates discovered** | Yes, quickly | Days. Low value on its own — discovery is the cheap part |
| 5,000 **qualified prospects** (DA/Spam/fit passed) | Needs ~15,000–50,000 candidates screened at typical pass rates, and 5,000 DA/Spam lookups — which under Option A is a lot of manual export | Weeks of screening, bounded by the metrics source |
| 5,000 **approved sends** | At ≤ 25/day and 1 per domain, this is **≈ 200 sending days** | ~10 months at pilot caps. Not a quarter |
| 5,000 **verified links** | Not plausible at any realistic conversion rate | — |

**Proposal:** retire "5,000 leads" as a delivery target and replace it with a rate, measured from
Stage C: *qualified prospects per week*, *approved sends per week*, and *verified links per
quarter*, each with the conversion rate between them. Then any volume ambition can be derived
from capacity instead of asserted.

## 7. Ownership and alignment with Shrey

One plan, not parallel versions. Proposed split, to confirm with Shrey on 26 October:

| Area | Proposed owner |
|---|---|
| System architecture, contracts, deterministic gates, audit | Abhinav |
| Pilot execution: target list, screening review, draft quality, reply handling | **[TBD — needs your input]** depends on Shrey's remit |
| Metrics source and manual CSV export (Option A) | **[TBD]** |
| Weekly funnel reporting | Abhinav produces, Avdhesh reviews |
| Accepting reviewer / go-no-go at each stage gate | Avdhesh |

**[TBD — needs your input]:** Shrey's intended scope and his 26 October first-output plan, so the
two are merged rather than run in parallel. I will align the PRD to whatever we agree and keep a
single requirements register.

## 8. Baseline — the one number I cannot supply

Stage C's conversion rates are only meaningful against the current manual process:

- **[TBD — needs your input]** links acquired per month manually today
- **[TBD]** hours per acquired link today
- **[TBD]** current manual reply rate, if tracked

Without these, the pilot can report its own absolute numbers but cannot claim improvement. I
would rather collect them from whoever runs outreach today than estimate them.

## 9. Proposed schedule

Dates assume agreement in the week of 27 October and are conditional on §3's prerequisites.

| Stage | Duration | Depends on |
|---|---|---|
| Align with Shrey; confirm scope, ownership, baseline | 2 days | This document being reviewed |
| Finish I2 acceptance suite | ~3 days | — |
| **Stage A** dry run | 2 days | I2 complete |
| Real email sender + credentials; close role-separation blocker | ~4 days | Sending identity decision |
| **Stage B** shadow run | 1 week | Stage A passed; metrics source chosen |
| **Stage C** live capped sends | 3 weeks | Stage B go/no-go; all four prerequisites |
| First full funnel report with real rates | — | End of Stage C |

Earliest credible date for real reply data: **approximately 5–6 weeks** from agreement. I would
rather state that than commit to a date the prerequisites cannot support.
