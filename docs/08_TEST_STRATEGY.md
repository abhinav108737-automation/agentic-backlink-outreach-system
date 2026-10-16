# Test Strategy

Unit: canonicalization, dedupe, hard policy, ranking, timing, suppression, idempotency, reporting, ROI, optimization lifecycle.
Contract: Pydantic models, OpenAPI, generated TS types, provider interfaces, agent structured outputs.
Integration: PostgreSQL, Qdrant, Ollama, scheduler, mock email/crawler/search/SEO, reporting.
Agent evals: relevance, contact choice, grounded outreach, reply classification, negotiation routing, verification ambiguity, NO_ACTION, malformed output, calibration.
E2E fixture: discover → qualify → research → contact → draft → mock send → simulated reply → negotiation/follow-up → mock backlink → verify → weekly report → learning → optimization proposal.
Security: secret leakage, prompt injection, tool scope, email-policy bypass, opt-out/rate-limit bypass, duplicate sends, Electron IPC/CSP/preload.
Packaging: clean install, first run, startup, upgrade/migration, uninstall policy, Windows/macOS smoke tests.
