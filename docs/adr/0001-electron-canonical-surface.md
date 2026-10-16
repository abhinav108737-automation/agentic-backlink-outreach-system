# ADR-0001: Electron is the canonical product surface

Status: Accepted. Date: 2026-10-16

## Context
Operators, managers and executives need one local application covering campaigns, prospects, outreach, replies, backlinks, reports, policy and health. The system runs entirely on local infrastructure (PostgreSQL, Qdrant, Ollama) with no hosted multi-tenant backend, and CLAUDE.md fixes Electron + React + TypeScript as non-negotiable.

## Decision
Electron is the canonical delivery surface, not a wrapper added later. React + TypeScript (strict) in the renderer, built with electron-vite and packaged with electron-builder. The renderer never imports privileged Node/Electron APIs: `contextIsolation=true`, `nodeIntegration=false`, `sandbox=true`, strict CSP, and a minimal typed preload. All business and data operations go to the local FastAPI backend over loopback (ADR-0014).

## Consequences
- One desktop codebase; no separate web deployment in v1.
- Packaging the Python backend, PostgreSQL and Qdrant becomes a real problem, deferred to ADR-0019.
- UI work (Lane A) can proceed against mocked HTTP from day one.
- Accessibility and cross-platform behavior must be tested per target OS.
