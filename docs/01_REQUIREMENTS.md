# Product & Functional Requirements

FR-01 Campaign Management — target pages, topics, keywords, policies, limits, sender identity, follow-up policy and status.
FR-02 Prospect Discovery — configurable search providers/strategies with provenance.
FR-03 Deduplication — canonicalize domains/URLs and prevent duplicate contact.
FR-04 Qualification — deterministic eligibility before ranking; initial DA > 45 and Spam Score < 1.
FR-05 Opportunity Ranking — relevance, authority, visibility, editorial fit, contactability, guest-post probability, history, priority and risk.
FR-06 Website Research — extract topics, editorial/contact pages, authors, contribution signals, outbound-link patterns and evidence.
FR-07 Contact Discovery — candidate contacts with confidence and provenance.
FR-08 Personalization — evidence-grounded drafts; no fabricated claims; prompt/model/version traceability.
FR-09 Outreach Execution — typed provider, dry-run, idempotency, limits, suppression, opt-out, bounded retries and audit.
FR-10 Follow-up Scheduling — deterministic timers; cancel on reply/opt-out/bounce/block.
FR-11 Reply Ingestion & Classification — classify, extract next action and route.
FR-12 Negotiation Support — draft responses/recommendations; configurable human review for commitments.
FR-13 Backlink Verification — target/source/anchor/context/live/canonical/redirect and durability checks.
FR-14 Weekly Reporting — executive + operational funnel, ROI, agent performance, failures and optimization proposals.
FR-15 Learning — outcomes, features, evaluation datasets.
FR-16 Optimization — proposals for ranking, thresholds, priorities, search, outreach, subject lines, timing, contact rules and prompts/skills.
FR-17 Promotion Governance — PROPOSED → OFFLINE_TEST → SHADOW → CANARY → EXPERIMENT → PROMOTED/REJECTED.
FR-18 Explainability — evidence, scores, policy version, provider/model and rationale.
FR-19 Audit — actor, time, correlation, old/new state and reason for mutations.
FR-20 Operations UI — dashboard, campaigns, prospects, outreach, replies, verification, reports, agent runs, policy, experiments and health.

## Non-functional
Reliability: idempotent, bounded retries, recoverable failures.
Security: least privilege, local secure secrets, no credential leakage.
Performance: target 1,000 prospect evaluations/day where providers permit.
Observability: structured logs, correlation IDs, health/readiness and job state.
Maintainability: typed provider boundaries, migrations, ADRs.
Testability: mocks/fixtures for every external provider.
Portability: Windows/macOS Electron packaging.
