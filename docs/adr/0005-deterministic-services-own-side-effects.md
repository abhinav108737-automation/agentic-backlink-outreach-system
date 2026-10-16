# ADR-0005: Deterministic services own external side effects

Status: Accepted. Date: 2026-10-16

## Context
An LLM must never be able to send an unwanted email, bypass suppression, exceed a rate limit or delete audit history. CLAUDE.md requires deterministic enforcement of eligibility, suppression, limits, idempotency, audit and verification.

## Decision
Agents reason and draft; deterministic services execute. Every external side effect (email send, crawl, search, verification, DB mutation) goes through a deterministic service that owns validation, policy, idempotency and audit. Agents reach tools only through a ToolGateway that enforces a per-agent allowlist (contract C4), runs the policy pre-check, records `tool_calls` and writes audit events. Agents never set lifecycle status columns and never receive raw DB, shell, unguarded send, policy-override or audit-deletion capability. An Operator `SEND` action is a request, re-checked by the policy gate before execution.

## Consequences
- Every new tool needs an allowlist entry and a policy decision path.
- Policy checks run twice on sends (enqueue and immediately before send), accepting duplicate work for safety.
- Agent output quality problems degrade drafts, never safety.
