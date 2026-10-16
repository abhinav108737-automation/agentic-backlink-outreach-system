# ADR-0019: Cross-platform packaging, macOS first

Status: Proposed (finalized during V1). Date: 2026-10-16

## Context
The development machine is macOS, but Ken Research end users may be primarily on Windows. The harder question is unresolved: how a packaged desktop app ships a Python backend, PostgreSQL and Qdrant. Docker is acceptable for development but not as an end-user requirement.

## Decision
macOS is the first packaging and smoke-test target, chosen for development speed only. The architecture stays cross-platform and **no macOS-specific architectural decision is permitted** (no Homebrew assumptions, no mac-only paths, no Darwin-only APIs; all paths go through a platform-aware data-directory helper). Windows packaging is required before production rollout if end users operate primarily on Windows, and the CI packaging matrix is added then.

How the backend, PostgreSQL and Qdrant are distributed is deferred to a V1 spike comparing: a bundled Python runtime (PyInstaller or a shipped interpreter) with an embedded PostgreSQL and a local Qdrant binary; an embedded alternative for vectors; or a documented local-services prerequisite. The spike's outcome updates this ADR to Accepted.

## Consequences
- The MVP is not blocked on packaging.
- Cross-platform discipline is enforced by review from the start, so Windows support is not a port.
- Installer size and first-run time are expected to be significant and must be measured in the spike.
