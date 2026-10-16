# ADR-0013: Canonical requirements registry and traceability

Status: Accepted. Date: 2026-10-16

## Context
`docs/01` used FR-01…FR-20 and the PRD used FR-01…FR-25, with the same IDs meaning different things (FR-04 was Qualification in one and SEO-signal collection in the other). Duplicate IDs make traceability and acceptance meaningless.

## Decision
The PRD/TRD is the functional baseline: FR-01…FR-25 and NFR-01…NFR-12 keep their IDs. Additions: FR-26 (ranking), FR-27 (autonomy modes and kill switch), FR-28 (missing-metric DEFER), FR-29 (GA4/GSC), FR-30 (provider registry with paid opt-in), FR-31 (first-run setup), FR-32 (memory service), and SEC-01…SEC-08. Old `docs/01` IDs are retired, with a complete mapping table in docs/01.

Every requirement records scope (MVP or V1), owning lane and an acceptance test. Tests carry IDs (`@pytest.mark.req("FR-09")`). A registry check fails on duplicate IDs; rows without tests warn until MVP and fail afterwards.

## Consequences
- Acceptance is mechanically checkable against the registry.
- References to retired IDs must be translated through the mapping table.
- New requirements take the next free ID and are never reused.
