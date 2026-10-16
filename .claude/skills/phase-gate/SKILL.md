---
name: phase-gate
description: Validate the current implementation phase before moving to the next phase.
---
1. Identify phase deliverables.
2. Run lint/types/unit/integration/migration/contract checks.
3. Ask qa-engineer for independent review.
4. Ask security-reviewer when new external I/O/auth/browser/email/privilege is added.
5. Compare to acceptance criteria.
6. Fix blockers.
7. Write `docs/phase-reports/<phase>.md`.
8. Create git checkpoint only after green.
