# Contracts changelog

Every entry corresponds to a contract version. Changes reach this file only through the
Contract Change Request process in `docs/14_CONTRACTS_AND_PROVIDERS.md` §"Contract change process":
a lane files a CCR with the Lead, the Lead edits `contracts/`, bumps the version, adds the entry
here and regenerates the OpenAPI snapshot and the TS client. Lanes never edit `contracts/**`
or `contracts/generated_ts/**` directly (ADR-0016).

## 0.1.0 — 2026-10-16 — FROZEN

Frozen at the Foundation Gate as implemented. This is the baseline all lanes A–F build against.

Contents: C1 enums and the 9 state-machine transition tables · C2 domain DTOs · C3 agent
input/output models · C4 per-agent tool allowlist and forbidden capabilities · C5 provider
protocols and error taxonomy · C6 policy gate with 14 block codes · C7 events · C8 HTTP API
surface (health typed; PRD §22 routes pre-registered as 501 stubs) · C9 generated TS client ·
C10 Electron bridge surface · C11 typed settings · C12 fixture formats · C13 logging and
redaction.

Changes folded in before the freeze (nothing consumed 0.1.0 yet, so the version was not bumped):
- message state machine: added the CANCELLED edges from pre-send states.
- workflow state machine: added WAITING → FAILED.
- `Settings.real_send_enabled`: now refuses real send while `autonomy_mode` is `shadow`.
- `Settings.qdrant_url`: default is the canonical service port 6333; a dev host that already
  has 6333 taken overrides `QDRANT_URL` (and `QDRANT_HOST_PORT` for the compose stack).

After this point: no change to `contracts/**` without a CCR, a version bump and an entry here.
