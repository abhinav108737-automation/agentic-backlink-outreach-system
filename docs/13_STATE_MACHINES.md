# Bounded State Machines

Status: canonical (ADR-0012). There is no single "giant" status enum. Each aggregate has its own bounded machine.

## Enforcement rules (all machines)
- Transition tables live in `kb_contracts.state_machines` as data (frozen contract C1).
- Each aggregate exposes exactly one service-level `transition(entity, to_state, *, actor, reason, correlation_id)`:
  1. rejects any pair not in the table with `InvalidTransition`;
  2. evaluates guards (policy, approvals, risk class);
  3. bumps `version` (optimistic lock; concurrent conflicting transitions fail);
  4. writes the `audit_events` row in the **same DB transaction**;
  5. emits a domain event.
- Agents never set status columns. They return typed decisions; deterministic services transition.
- **Tests:** an exhaustive (from, to) matrix per machine (every pair not listed must raise), guard tests per guarded edge, and Hypothesis property tests (random walks never reach an illegal state; terminal states have no outgoing edges except those listed).

Notation: `A → B | C` means A may go to B or C. "Terminal" = no outgoing edges unless listed.

## 1. Prospect lifecycle (`prospects.status`)
| From | To | Guard / note |
|---|---|---|
| DISCOVERED | ELIGIBLE, REJECTED, DEFERRED | deterministic hard eligibility; DEFERRED when a required metric is missing (ADR-0015) |
| DEFERRED | ELIGIBLE, REJECTED | metrics arrived / manual import |
| ELIGIBLE | RANKED | ranking result stored |
| RANKED | RESEARCHED | dossier stored |
| RESEARCHED | CONTACT_READY, REJECTED | contact channel found / research finds unfit |
| CONTACT_READY | OUTREACH_READY | sequence planned |
| OUTREACH_READY | IN_OUTREACH | first message sent (mock or real) |
| IN_OUTREACH | WON, LOST | opportunity outcome |
| REJECTED | DISCOVERED | only via re-qualification under a **new** policy version |
| LOST | ELIGIBLE | only if policy allows re-contact after cooldown |
| any non-terminal | SUPPRESSED | suppression entry matches domain/contact |

Terminal: WON, SUPPRESSED.

## 2a. Outreach sequence lifecycle (`outreach_sequences.status`)
| From | To | Guard / note |
|---|---|---|
| PLANNED | READY, POLICY_BLOCKED | first message QA + policy gate |
| POLICY_BLOCKED | READY, CANCELLED | re-evaluation passes / operator cancels |
| READY | WAITING_REPLY | first message SENT; follow-up job scheduled |
| WAITING_REPLY | FOLLOW_UP_DUE | follow-up timer fired |
| FOLLOW_UP_DUE | WAITING_REPLY, COMPLETED_NO_REPLY | next step sent / steps exhausted |
| PLANNED, READY, POLICY_BLOCKED, WAITING_REPLY, FOLLOW_UP_DUE | REPLIED, OPTED_OUT, BOUNCED, CANCELLED | inbound event / operator / suppression |

Terminal: REPLIED, OPTED_OUT, BOUNCED, CANCELLED, COMPLETED_NO_REPLY.
Guard: entering any terminal state cancels the sequence's pending `scheduled_jobs` **in the same transaction**. OPTED_OUT and BOUNCED also create `suppression_entries`.

## 2b. Outreach message lifecycle (`outreach_messages.status`)
| From | To | Guard / note |
|---|---|---|
| DRAFT | QA_PASSED, QA_FAILED | every factual claim maps to evidence; similarity threshold |
| QA_FAILED | DRAFT | revision |
| QA_PASSED | PENDING_APPROVAL, APPROVED | approval required by mode/policy (guarded) |
| PENDING_APPROVAL | APPROVED, REJECTED | operator decision (`approval_requests`) |
| APPROVED | QUEUED, POLICY_BLOCKED, CANCELLED | policy gate re-checked at enqueue |
| QUEUED | SENDING, CANCELLED, POLICY_BLOCKED | policy gate re-checked immediately before send; kill switch |
| SENDING | SENT, RETRY_WAIT, FAILED | provider result; idempotency key |
| RETRY_WAIT | QUEUED | attempts < max (bounded) |
| SENT | DELIVERED, BOUNCED | provider events |

Terminal: DELIVERED, BOUNCED, FAILED, REJECTED, CANCELLED, POLICY_BLOCKED (a new message version is drafted instead).
Crash recovery: a message found in SENDING at startup is reconciled with the provider using its idempotency key before any retry. Never send twice.

## 3. Opportunity / negotiation lifecycle (`guest_post_opportunities.status`)
| From | To | Guard / note |
|---|---|---|
| OPEN | NEGOTIATING, LOST | created on positive/negotiation reply |
| NEGOTIATING | AWAITING_APPROVAL, ESCALATED_TO_HUMAN, AGREED, LOST | AWAITING_APPROVAL when a commitment is high-risk (ADR-0007) |
| AWAITING_APPROVAL | NEGOTIATING, AGREED, LOST | approval decision |
| ESCALATED_TO_HUMAN | NEGOTIATING, AGREED, LOST | human takeover outcome |
| AGREED | CONTENT_IN_PROGRESS, LOST | |
| CONTENT_IN_PROGRESS | CONTENT_SUBMITTED, LOST | |
| CONTENT_SUBMITTED | REVISION_REQUESTED, PUBLISHED, LOST | PUBLISHED creates a `backlinks` row in EXPECTED |
| REVISION_REQUESTED | CONTENT_SUBMITTED, LOST | |

Terminal: PUBLISHED, LOST.

## 4. Backlink lifecycle (`backlinks.status`)
| From | To | Guard / note |
|---|---|---|
| EXPECTED | FOUND, MISSING | link detected / not published by deadline |
| FOUND | VERIFIED, CHANGED, MISSING | full checks: target, anchor, rel, HTTP status, canonical |
| VERIFIED | CHANGED, MISSING | recheck result |
| CHANGED | VERIFIED, MISSING | |
| MISSING | VERIFIED, FOUND, LOST | LOST after N consecutive misses or configured window |
| LOST | FOUND | link reappeared (audited) |

Only VERIFIED counts as an active backlink in reports. Never count MISSING/LOST as active.

## 5. Optimization lifecycle (`optimization_proposals.status`)
`PROPOSED → OFFLINE_TEST → SHADOW → CANARY → EXPERIMENT → PROMOTED / REJECTED → MONITORED → ROLLED_BACK`

| From | To | Guard / note |
|---|---|---|
| PROPOSED | OFFLINE_TEST, REJECTED | |
| OFFLINE_TEST | SHADOW, REJECTED, PROMOTED | PROMOTED only for **L1** with passing offline evaluation |
| SHADOW | CANARY, EXPERIMENT, REJECTED | |
| CANARY | EXPERIMENT, PROMOTED, REJECTED | PROMOTED allowed for L2 after SHADOW + CANARY pass |
| EXPERIMENT | PROMOTED, REJECTED | experiment result with confidence interval and guardrails |
| PROMOTED | MONITORED | automatic on activation of the new version |
| MONITORED | ROLLED_BACK | guardrail breach or operator rollback |

Risk-class guards (ADR-0006):
- **L1** (ordering, query variants, retry strategy, internal priority): may promote after OFFLINE_TEST.
- **L2** (subject lines, templates, ranking weights, timing, contact prioritization): must pass SHADOW and then CANARY or EXPERIMENT.
- **L3** (hard qualification thresholds, suppression, send limits, compliance, binding negotiation policy): PROMOTED requires an APPROVED `approval_requests` row **and** a passing evaluation. Never silent.

Terminal: REJECTED, ROLLED_BACK. Every promotion records evidence, evaluator, versions and rollback target.

## 6. Workflow and job lifecycle
**`workflow_runs.status`**
| From | To |
|---|---|
| PENDING | RUNNING, CANCELLED |
| RUNNING | WAITING, SUCCEEDED, FAILED, CANCELLED |
| WAITING | RUNNING, CANCELLED |

Terminal: SUCCEEDED, FAILED, CANCELLED. WAITING covers timers, human approvals and external events.

**`scheduled_jobs.status`**
| From | To | Guard / note |
|---|---|---|
| SCHEDULED | RUNNING, CANCELLED | claimed with `FOR UPDATE SKIP LOCKED`; lease set |
| RUNNING | SUCCEEDED, RETRY_WAIT, DEAD, SCHEDULED | SCHEDULED only on lease expiry (attempts += 1) |
| RETRY_WAIT | SCHEDULED, CANCELLED | backoff elapsed |

Terminal: SUCCEEDED, DEAD (dead-letter, surfaced for human review), CANCELLED.

## Auxiliary: campaign lifecycle (`campaigns.status`)
DRAFT → ACTIVE; ACTIVE ⇄ PAUSED; ACTIVE | PAUSED → ARCHIVED. Terminal: ARCHIVED. PAUSED blocks all sends (PolicyEngine hard block).
