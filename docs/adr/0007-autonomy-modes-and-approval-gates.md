# ADR-0007: Autonomy modes and human approval gates

Status: Accepted. Date: 2026-10-16

## Context
Outbound email carries reputational and compliance risk. The system must be safe by default while still allowing progressive automation.

## Decision
Three modes: **shadow** (compute decisions and drafts, send nothing — the development and first-run default), **guarded** (low-risk policy-compliant actions execute automatically, higher-risk steps need review — required for the first real-email release), **autonomous** (all policy-eligible actions execute within budgets and limits — explicit enablement only).

`REAL_SEND_ENABLED=false` by default. Human approval is always required for first-time live-send activation, high-risk negotiation commitments, and L3 policy changes. Routine follow-up approval is configurable only after the policy and evaluation gates are proven. A global kill switch halts all sending immediately, and mode plus kill-switch changes are audited.

## Consequences
- Approvals are a first-class entity (`approval_requests`) with UI, not an ad-hoc flag.
- Throughput in guarded mode is bounded by operator review capacity.
- Enabling real sending is a deliberate, recorded act.
