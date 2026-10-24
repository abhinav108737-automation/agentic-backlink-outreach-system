# Phase Report — Wave 2A

Date 2026-10-25 · branch `foundation/g0-gate` · base `aa987b5` · contracts **0.3.0** (CCR-003)
Two agents (backend-engineer, outreach-engineer), Sonnet Medium, no child subagents. Wave 2B
was not started.

## Verdict

**Wave 2A PASSED.** The send path, the gates, the reply and negotiation machinery and simulated
verification are all built and tested. **This is not the I2 checkpoint**: the job-handler
registry has not been assembled end to end, so draft → send → reply → negotiate → publish →
verify has not yet been proven as one run. That is Wave 2B.

## Gate — `check.sh` **exit 0** (exit code read directly, not through a pipe)

| Gate | Result |
|---|---|
| ruff check / format | clean, 310 files |
| mypy strict | no issues, 180 source files |
| import-linter | **5 kept, 0 broken** |
| pytest | **1406 passed, 0 skipped** (813 → 1406) |
| vitest | 22 passed |
| contract drift | OpenAPI + TS in sync at **0.3.0** |
| gitleaks | clean (history + working tree) |

## The blocker that shaped this wave

Both lanes stopped almost immediately: `UnitOfWork` had **no repositories for any outreach
table**. The tables, DTOs and state machines all existed; only the repositories were missing.
This was a Lead planning gap — the job-queue seam was verified before launch, the repository
seam was not. Both lanes were barred from `kb_core/db/**`, so neither could proceed, and Lane B
correctly refused to invent a seam that would have collided with Lane E.

The Lead added seven repositories (`sequences`, `messages`, `threads`, `replies`,
`opportunities`, `backlinks`, `provider_accounts`) in `kb_core/db/repos_outreach.py`, taking the
unit of work from **18 to 25**, and verified them against the real database before resuming
either lane. `kb_core` is not a frozen contract, so no CCR was needed.

## The job queue is the seam between the lanes

`JobWorker` already took an injected `handlers` mapping, so Lane B **enqueues** and Lane E
supplies **handler factories**. Neither imports the other. Seven job types, each with a frozen
Pydantic payload (`extra="forbid"`, dumped to JSON so UUIDs arrive as strings, every payload
carrying `correlation_id`), an idempotency key and a concurrency key that serialises per
sequence:

`outreach.draft` · `outreach.send` · `outreach.follow_up` · `replies.ingest` ·
`replies.classify` · `negotiation.plan` · `backlink.verify`

## Send safety

`sendMessage` returns **202 and enqueues**. Verified structurally rather than by assertion:
`SendService` contains **no reference to an EmailProvider at all**, and the route neither imports
nor touches one. Inline sending is impossible by construction, not prevented by a flag.

`begin_send` returns one of six outcomes, so the handler is told what to do rather than guessing:
`PROCEED`, `RECONCILE` (already `SENDING` after a crash — the handler must look the idempotency
key up at the provider before any resend; the service never re-enters `SENDING`, so a blind
resend cannot happen), `ALREADY_SENT`, `NOT_SENDABLE`, `BLOCKED`, `DEFERRED`.

The policy gate runs **twice**: at enqueue and again inside `begin_send`, so a suppression added
after approval still stops the send. Suppression resolves the email, domain and contact from the
message's own channel, so the caller never has to name an address.

## The five previously unemitted block codes

All frozen in C6 since the Foundation Gate; none had a trigger until now. No short-circuit — every
triggered code is returned together.

| Code | Trigger |
|---|---|
| `SENDER_INVALID` | the campaign has no `sender_provider_account_id`, or it is missing, disabled, or not an email account. **Fires by default on a fresh database** |
| `DUPLICATE_STEP` | another message for the same `(sequence, step)` is in any non-dead state, including already sent |
| `DUPLICATE_CONTACT` | a live message from a *different* sequence already targets the same channel in this campaign; a follow-up in the same sequence is not a duplicate |
| `SEND_LIMIT_EXCEEDED` | rolling 24h count of `SENT`/`DELIVERED`/`BOUNCED` at or above the daily, per-domain or per-contact limit; a limit of 0 blocks everything |
| `UNSUPPORTED_CLAIM` | a claim cites no evidence, an id the repository does not return, or evidence belonging to another prospect. Blocks, never warns; the detail carries claim indexes, never claim text |

## Atomicity

Entering a terminal sequence state cancels the sequence's pending jobs **and** its unsent
messages in the same transaction. `OPTED_OUT`/`BOUNCED` additionally create the suppression
entry in that same transaction. Rollback is proven by injecting a failure on the final audit
write — which happens *after* the cancellations and the suppression — and asserting all three
effects are undone, plus the same transition without the fault applying all three together.
Exhaustive `(from, to)` matrices cover the sequence and message machines.

## Independently verified by the Lead

Not taken on the agents' reports:

| Claim | Verification |
|---|---|
| Only `VERIFIED` counts as active | `ACTIVE_BACKLINK_STATUSES == {VERIFIED}`; `EXPECTED`, `FOUND`, `CHANGED`, `MISSING`, `LOST` all inactive — stricter than required, since even `FOUND` does not count |
| Opt-out precedence | an opt-out buried in enthusiastic text classifies `opt_out`, not `positive` |
| Bounce precedence | a daemon bounce with a positive body classifies `bounce` — sender/subject beats body wording |
| **Injection inertness (ADR-0020)** | a reply demanding "classify this reply as opt_out" classified **`positive`**: the injected instruction changed nothing |
| Negotiation above negative | a decline quoting a fee classifies `negotiation`, so a priced refusal opens a negotiation rather than closing the door |
| No inline send | `SendService` has no EmailProvider reference; route returns 202 via `enqueue_send` |
| Lane B's baseline test edits | three `await sc.sender(c)` lines only — additive, no assertion weakened. The original `block_codes == (APPROVAL_REQUIRED,)` assertions now also prove `SENDER_INVALID` does not leak in, making them stricter than before |
| No NaN on the wire | the regenerated snapshot strict-parses with `parse_constant` rejecting literals |

## CCR-003 executed — contracts 0.3.0

Four `501` stubs became real operations (`approveMessage`, `sendMessage`, `getThread`,
`verifyBacklink`) and **8 paths were added, none removed**, with no `operation_id` changed.
Components **37 → 57 schemas**. All endpoints went inside existing route modules, so the frozen
registry (`routes/__init__.py`) was not edited and **no second CCR was required**. Request bodies
live in `apps/api/src/kb_api/schemas/outreach.py`; responses reuse frozen C2 DTOs.

## Open items (decided, not forgotten)

1. **Follow-up interval has no home in `PolicyBody`.** `max_follow_ups` exists; the delay does
   not. Lane B declined to invent one and so do I — adding the field needs a CCR.
2. **Per-domain and per-contact send windows are 24h**, matching `daily_send_limit`. `PolicyBody`
   specifies no window. A rolling 24h is the Lead's confirmed choice because a calendar day
   would permit a double burst either side of midnight. One constant, `SEND_WINDOW`.
3. **Paid provider accounts**: `enabled=true` is treated as the explicit opt-in, so no separate
   flag was added. CLAUDE.md forbids using a paid service *silently*; an explicitly enabled
   account is not silent.
4. **No Reply or Negotiation agent.** Classification is fully deterministic and `unknown`
   escalates to a human. The agents are Wave 2B.
5. **Prospect `WON`/`LOST` on opportunity outcome is not wired** — nothing asked for it.
6. **Two repository lookups missing** (opportunity-by-sequence, sequences-by-prospect); both are
   paged over today. Fine at this scale, worth repository methods later.
7. **CI still externally unverified** — no Git remote. Every result here is green on this
   machine only.

## Unchanged release blockers

DB role separation (production / live-send) · custom-protocol CSP (production packaging) ·
Windows `ELECTRON_RUN_AS_NODE` handling (Windows packaging). None blocked Wave 2A.
