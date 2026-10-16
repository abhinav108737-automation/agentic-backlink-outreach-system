# Ken Research — Agentic AI Backlink Outreach System
## Claude Code Implementation Handoff

This package is the implementation handoff for Claude Code.

### Start
1. Extract into the root of a new Git repository.
2. Open the folder in VS Code.
3. Sign in to Claude Code using the user's existing Claude account.
4. Run `claude` from the repository root.
5. Read `CLAUDE.md`.
6. Read `MASTER_BUILD_PROMPT.md`.
7. Run `/plan` before implementation.
8. Use `.claude/agents/` for specialist subagents and agent teams/worktrees for independent parallel workstreams.
9. Keep real outbound sending disabled until mock flow, policy, QA and security gates pass.

### North-star loop
DISCOVER → QUALIFY → RESEARCH → FIND CONTACT → PERSONALIZE → OUTREACH → FOLLOW UP → INTERPRET → NEGOTIATE → ACQUIRE → VERIFY → REPORT → LEARN → OPTIMIZE → REPEAT

### Core decision
PydanticAI is the application-level Agentic Operator. Claude Code is the implementation environment used to build the application.
