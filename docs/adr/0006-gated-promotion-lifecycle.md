# ADR-0006: Gated promotion lifecycle with risk classes

Status: Accepted. Date: 2026-10-16

## Context
The learning layer proposes changes to ranking, thresholds, messaging and timing. Unreviewed promotion could silently weaken compliance or quality.

## Decision
Every optimization proposal moves through PROPOSED → OFFLINE_TEST → SHADOW → CANARY → EXPERIMENT → PROMOTED/REJECTED → MONITORED → ROLLED_BACK (docs/13 §5), with risk-class guards:
- **L1** (ordering, query variants, retry strategy, internal priority): may promote after OFFLINE_TEST when there is no external behavior or compliance impact.
- **L2** (subject lines, templates, ranking weights, timing, contact prioritization): must pass SHADOW and then CANARY or EXPERIMENT.
- **L3** (hard qualification thresholds, suppression, send limits, compliance rules, binding negotiation policy): never auto-promoted; requires an APPROVED `approval_requests` row plus a passing evaluation.

Every promotion records evidence, evaluator, versions and a rollback target. Policy changes are new immutable `policy_versions`, never edits.

## Consequences
- Learning gains are slower to land, deliberately.
- A regression test must prove L3 policy cannot be modified without an approval record.
- Rollback is always available because prior policy versions remain intact.
