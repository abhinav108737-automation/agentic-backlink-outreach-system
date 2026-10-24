# Handoff — entering Wave 2

Date 2026-10-23, updated after Wave 2A on 2026-10-25 · branch `foundation/g0-gate`
contracts **0.3.0** · tree clean · no Git remote

> **Wave 2A is complete** — see `docs/phase-reports/wave2a.md`. What remains for the I2
> checkpoint is Wave 2B: assemble the job-handler registry and prove draft → mock send →
> reply → negotiate → publish → verify as one run, plus the Outreach/Reply/Negotiation/
> Verification agents. The sections below describe the pre-Wave-2A position; the open-items
> list in `TASK_BOARD.md` and `wave2a.md` supersedes item lists here.

## Where things stand

I1 passed and has been stabilized. The agent-driven slice runs end to end:
Discovery → Qualification → Research → Contact → persistence → audit → API readback. Full detail
in `docs/phase-reports/i1.md`.

| Gate | Result |
|---|---|
| `check.sh` | **exit 0** (read directly, not through a pipe) |
| pytest | **813 passed, 0 skipped** |
| vitest | 22 passed |
| mypy strict | 144 source files, incl. `apps/runtime/src` |
| import-linter | **5 kept, 0 broken** |
| contract drift | OpenAPI + TS in sync at `0.2.0` |
| gitleaks | clean (history + working tree) |

## Architecture you are inheriting

`kb_runtime` (`apps/runtime/`) is the composition root and a **leaf**: it is the only package
that may see both the agent runtime and the database, and nothing imports it.

```
kb_runtime ──► kb_agents ──► kb_contracts
     │              └──► kb_core.{discovery,research,contacts}   (pure, no db)
     ├──► kb_core (services, db)   ├──► kb_providers   └──► kb_contracts
```

Five import-linter contracts enforce this, and `tests/contract/test_import_boundaries.py`
re-checks the load-bearing ones by AST — including imports nested inside functions, so a lazy
import or service locator cannot dodge them.

**Three rules to respect when extending:**
1. Add new tool adapters in `kb_runtime.capabilities`, never inside `kb_agents`. Agents reach
   persistence only through injected capabilities.
2. A capability must return exactly the result model declared in `kb_agents/tools/specs.py`.
   The gateway validates output and fails the call on a mismatch — it will not coerce.
3. When the API needs to trigger agent runs, **enqueue a job**; do not import `kb_agents` from
   `kb_api`. A separate worker process may import `kb_runtime` (ADR-0008/0017).

## What Wave 2 is

Checkpoint I2: draft → mock send → reply → negotiate → publish → verify.

The frozen contracts already cover it — `DraftMessage`/`DraftClaim`, `ReplyClassificationResult`,
`NegotiationPlan`, `VerificationAmbiguityResult`, the outreach/message/opportunity/backlink state
machines, and the remaining `BlockCode`s (`DUPLICATE_CONTACT`, `DUPLICATE_STEP`,
`SEND_LIMIT_EXCEEDED`, `SENDER_INVALID`, `UNSUPPORTED_CLAIM`, …) which Lane B deliberately left
unemitted. The Outreach/Reply/Negotiation/Verification agents and their tool allowlists already
exist in frozen C4, and `kb_agents/tools/specs.py` already has args models for their tools — but
**no capability is wired for any of them**, which is the first Wave 2 task.

Real send stays off: `REAL_SEND_ENABLED=false`, `AUTONOMY_MODE=shadow`, mock EmailProvider only.

## Open items, in the order they will bite

1. **`read_memory` returns no snippets.** MemoryService and the Qdrant retrieval path are
   unbuilt. Agents are currently exercised against empty memory on purpose.
2. **Two Ollama embedders exist** — `kb_providers/vector/embeddings.py` (Lane C) and
   `kb_providers/embedding/ollama.py` (Lane D). Real duplication; pick one.
3. **Two fixture domains are not registered domains** (`blog.datapulseanalytics.io`,
   `www.energyoutlookdaily.com`) while their metrics are keyed by host, so they DEFER as
   never-fetched. That is correct under ADR-0015. If you change it, change the **corpus**, not
   the lookup — a subdomain-guessing fallback could attribute one host's metrics to another.
4. **`LOST → ELIGIBLE` fails closed** with `RECONTACT_COOLDOWN_UNDEFINED`: `PolicyBody` has no
   cooldown field. Adding one needs a CCR.
5. **No HTTP routes for suppression** add/supersede; the service exists and is tested.
6. **Idempotency keys are honoured on campaign create only.**
7. **Qdrant rebuild CLI has never run against live Qdrant or Ollama**; it is tested against the
   in-memory provider only.
8. **CI has never run** — no Git remote (Foundation decision 4). Every result above is green on
   this machine only. Run the workflow on the first push, before trusting any merge.

## Release blockers (unchanged, none block I2)

- **Production / live-send:** DB role separation — the app connects as DB owner and can alter or
  drop its own append-only and suppression triggers.
- **Production packaging:** serve the renderer from a custom protocol so CSP is a real header.
- **Windows packaging:** a Windows-compatible way to clear `ELECTRON_RUN_AS_NODE`.

## Ground rules that have not changed

Contracts `0.2.0` are frozen — changes go through a CCR (`docs/14`, `contracts/CHANGELOG.md`,
`docs/ccr/`). PostgreSQL is authoritative; Qdrant is derived and rebuildable. No paid APIs, no
live email, no Redis/Celery/LangGraph/Elasticsearch/S3/Kubernetes. Agents never set status
columns and never get raw DB, shell, unrestricted HTTP, policy-override or direct-send access.
