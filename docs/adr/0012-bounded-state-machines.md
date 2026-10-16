# ADR-0012: Bounded state machines, no global status enum

Status: Accepted. Date: 2026-10-16

## Context
The PRD described one long lifecycle string mixing outreach, opportunity and backlink states; `docs/04` had partially overlapping enums. A single enum conflates unrelated concerns and makes invalid transitions untestable.

## Decision
Six bounded machines plus an auxiliary campaign machine (docs/13): prospect; outreach (split into sequence and message); opportunity/negotiation; backlink; optimization; workflow and job. The optimization machine is exactly PROPOSED → OFFLINE_TEST → SHADOW → CANARY → EXPERIMENT → PROMOTED/REJECTED → MONITORED → ROLLED_BACK.

Transition tables live in `kb_contracts.state_machines` as data. Each aggregate exposes one `transition()` that rejects unlisted pairs with `InvalidTransition`, evaluates guards, bumps `version`, writes the audit row **in the same transaction**, and emits a domain event. Agents never set status columns.

Tests: an exhaustive (from, to) matrix per machine where every unlisted pair must raise, guard tests per guarded edge, and property tests over random walks.

## Consequences
- Illegal states become test failures rather than production anomalies.
- A terminal sequence state cancels pending jobs atomically, so follow-ups cannot outlive an opt-out.
- Adding a state means updating the table, the matrix test and this family of documents.
