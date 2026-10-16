# ADR-0011: Canonical data model and naming normalization

Status: Accepted. Date: 2026-10-16

## Context
Two incompatible table sets existed. `docs/04` v0 lacked a suppression table even though suppression is a non-negotiable control; the PRD lacked several operational tables. The same concept appeared under different names (`prospect_research` vs `research_dossiers`, `reply_events` vs `replies`, `email_events` vs `outreach_events`).

## Decision
Adopt the PRD model as the baseline, normalized into the single schema in docs/04, with no duplicate concepts under different names. Key points:
- `domains` is a global entity separate from `prospects`, enabling cross-campaign dedupe, metrics and suppression.
- `suppression_entries` is explicit, never deleted; lifting is an audited supersession.
- `model_runs` is merged into `agent_runs`; `tool_calls` and `operator_decisions` are separate.
- Added for operational completeness: `approval_requests`, `operators`, `system_settings`, `idempotency_records`, `feature_snapshots`, `embedding_index_records`.
- Conventions: UUIDv7 keys, UTC `timestamptz`, `version` for optimistic locking, plural snake_case, archive over delete, `correlation_id` on mutation-bearing rows, PII minimization, secrets only as `secret_ref`.
- Append-only: `audit_events` (DB trigger blocks UPDATE/DELETE), `outreach_events`, `domain_metrics`, `backlink_verifications`, `policy_versions`, `report_snapshots`.
- The idempotency key for sends is `campaign_id + contact_id + step_no + message_version`, UNIQUE.

Names are normalized **before** migration 001; the full old-to-new map is in docs/04.

## Consequences
- Migration 001 creates the whole schema at once, so lanes can build against stable tables.
- Schema changes after the freeze are Lane C requests with a new migration.
- Append-only enforcement makes some corrections require compensating rows rather than edits.
