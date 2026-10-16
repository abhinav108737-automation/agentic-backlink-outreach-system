# ADR-0008: PostgreSQL-backed job queue; DBOS evaluated by spike

Status: Proposed (finalized after the Wave 1 spike). Date: 2026-10-16

## Context
Follow-ups, rechecks, weekly reports, retries and long-running workflows need durable scheduling that survives restarts. PostgreSQL is already a hard dependency. Redis appeared only in a diagram belonging to a different project.

## Decision
For v1, implement durable scheduling on PostgreSQL: a `scheduled_jobs` table claimed with `SELECT … FOR UPDATE SKIP LOCKED`, with leases and heartbeats, bounded retries with backoff, a UNIQUE idempotency key, a concurrency key for rate control, and a dead-letter state surfaced for human review. The worker runs in-process with the backend. Expired leases return a job to SCHEDULED with an incremented attempt count.

DBOS gets a time-boxed (4 h) spike in Wave 1. It is adopted only if it materially simplifies scheduled follow-ups, long-running workflow recovery, retries, durable checkpoints and concurrency/rate control, while remaining packageable into a desktop app. Redis and Celery are not introduced (ADR-0017).

## Consequences
- One fewer runtime service; scheduling is backed up with the database.
- Polling latency and throughput limits are accepted at desktop scale.
- The `JobQueue` protocol keeps DBOS adoptable without changing callers.
- This ADR is updated to Accepted or Superseded after the spike.
