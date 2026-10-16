# Learning & Optimization

Memory = what happened. Learning = what history implies. Optimization = what should change.

Inputs: discovery, qualification, message variants, delivery/bounce, replies, opportunities, response time, negotiation outcomes, backlinks, persistence, SEO outcomes, agent calibration, errors/exceptions.

Outputs: feature snapshots, performance summaries, pattern candidates, calibrated confidence and optimization proposals.

Promotion: PROPOSED → OFFLINE_TEST → SHADOW → CANARY → EXPERIMENT → PROMOTED/REJECTED → MONITORED → ROLLED_BACK if needed.

Transitions and guards: docs/13 §5 (ADR-0006, ADR-0012).

Risk classes:
- **L1 — low risk** (ordering, query generation, retry strategy, internal priority): may auto-promote after passing OFFLINE_TEST if there is no external behavior or compliance impact.
- **L2 — behavioral** (subject lines, templates, timing, ranking weights, contact prioritization): must pass SHADOW and then CANARY or EXPERIMENT before promotion.
- **L3 — governed** (hard qualification thresholds, suppression/opt-out, sending limits, compliance rules, binding negotiation policies): never auto-promoted; requires an approved `approval_requests` row and a passing evaluation.

Metrics: qualified-prospect precision, positive reply rate, guest-post agreement, verified backlink rate, time/cost per backlink, persistence, calibration error, NO_ACTION accuracy, policy violation rate = 0.
