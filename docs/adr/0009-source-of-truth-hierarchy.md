# ADR-0009: Source-of-truth hierarchy and document reconciliation

Status: Accepted. Date: 2026-10-16

## Context
The handoff pack carried overlapping specifications that conflicted on the data model, requirement IDs, state machines, repository layout and timeline: CLAUDE.md, a detailed PRD/TRD, the `docs/` set, README-style handoff files and a reference image belonging to a different project (Internal Linking).

## Decision
When sources disagree, authority runs highest first:
1. CLAUDE.md and the canonical architecture
2. `docs/reference/source/Agentic_Backlink_Outreach_PRD_TRD_v1.0.docx`
3. `docs/*.md`
4. README and handoff support files
5. Older reference material, for methodology only

The PRD/TRD wins on implementation detail unless it violates an explicit CLAUDE.md non-negotiable. `docs/*.md` were rewritten in place to the reconciled content so no duplicate source survives. The canonical diagram is `reference/architecture_reference.png` (backlink outreach); the Internal Linking diagram is retained under `docs/reference/methodology/` with an explicit not-this-project filename.

Two definitions of done apply: **MVP DONE** (fixture/mock-driven local end-to-end loop with full audit history and passing tests) and **V1 DONE** (real approved providers, local-model-only path, packaging, backup/restore, provenance, hardening, runbooks) — docs/11.

## Consequences
- Material reconciliations are recorded as ADRs 0010–0013.
- Source documents stay in the repository for provenance but are excluded from packaging.
- Future conflicts resolve by this ranking without renegotiation.
