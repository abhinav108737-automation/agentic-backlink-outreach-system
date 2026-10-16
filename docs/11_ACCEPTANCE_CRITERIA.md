# Acceptance Criteria

Two gates (Decision 16, ADR-0009). Requirement IDs refer to docs/01.

## MVP DONE
A fixture/mock-driven local end-to-end workflow runs:
discover → qualify → research → contact → draft → mock send → reply → negotiate → verify → report → learn → optimization proposal,
with complete audit history and passing tests (lint, types, unit, integration, contract, migration, fixture E2E). CI needs no live credentials and no Ollama.

## V1 DONE
MVP plus: real approved providers (disabled by default, explicitly enabled), local-model-only end-to-end on Ollama (FR-21), packaging (macOS first; Windows before production rollout if required), backup/restore and Qdrant rebuild (FR-25), repository provenance records (SEC-08), security hardening, and validated deployment runbooks.

## Area criteria
Product: local campaign runs end-to-end; workflow visible; weekly report generated; decisions explainable.
Discovery: provider abstraction, dedupe, provenance, failure handling.
Qualification: configurable thresholds; hard blocks deterministic; ranking explainable; missing required metrics ⇒ DEFERRED, never passed.
Agentic: Operator delegates; outputs typed; tool scope enforced; runs persisted; Ollama/local path works.
Outreach: mock default; no duplicate sends; limits/suppression/opt-out enforced; replies cancel invalid follow-ups; real send explicit and approved.
Verification: link presence/status and repeated durability checks; only VERIFIED counts as active.
Reporting: reproducible weekly snapshot; numbers deterministic; ROI estimate labels.
Learning: outcome collection; proposal generation; offline test; versioned promotion with risk-class guards; rollback.
State machines: every bounded machine rejects invalid transitions (tested).
Engineering: tests/lint/types/migrations pass; no secrets; no mandatory paid API; setup complete; installer works on target OS (V1).
