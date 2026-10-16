# Test Strategy

Unit: canonicalization, dedupe, hard policy (incl. missing-metric DEFER), ranking, timing, suppression, idempotency, reporting, ROI, optimization lifecycle.
State machines: exhaustive invalid-transition matrix, guard tests and property tests for every bounded machine (docs/13).
Contract: Pydantic models, OpenAPI, generated TS types, provider interfaces, agent structured outputs.
Integration: PostgreSQL, Qdrant, Ollama, scheduler, mock email/crawler/search/SEO, reporting.
Agent evals: relevance, contact choice, grounded outreach, reply classification, negotiation routing, verification ambiguity, NO_ACTION, malformed output, calibration.
E2E fixture: discover → qualify → research → contact → draft → mock send → simulated reply → negotiation/follow-up → mock backlink → verify → weekly report → learning → optimization proposal.
Security: secret leakage, prompt injection, tool scope, email-policy bypass, opt-out/rate-limit bypass, duplicate sends, Electron IPC/CSP/preload.
Security (localhost): missing/wrong token rejected, loopback-only bind, CORS deny, Host allowlist, token absent from logs, renderer cannot read token.
Traceability: tests carry requirement IDs from docs/01 (`@pytest.mark.req`).
CI uses PydanticAI TestModel/FunctionModel and mock providers; Ollama runs only in optional local integration and the V1 local-model-only E2E.
Packaging: clean install, first run, startup, upgrade/migration, uninstall policy; macOS smoke tests first, Windows before production rollout if required.
