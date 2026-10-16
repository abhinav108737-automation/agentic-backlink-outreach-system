# Implementation Plan & TAT

Phase 0 Foundation — 1–2 days: monorepo, Electron/React/TS, FastAPI, PostgreSQL/Alembic, Qdrant, Docker, logging/config, tests/CI, typed API.
Phase 1 Core Data + Campaign Control — 1–2 days: campaigns, prospects, lifecycle, policies, audit, dashboard skeleton.
Phase 2 Discovery/Qualification/Research — 2–4 days: search/crawler/SEO interfaces, SearXNG/Crawl4AI adapters, dedupe, ranking, evidence store and UI.
Phase 3 PydanticAI Operator + Agents — 2–4 days: model abstraction, Ollama, operator, specialist agents, typed tools, run persistence, fallbacks and evals.
Phase 4 Contact/Outreach/Replies — 2–4 days: contacts, message QA, email abstraction, mock/real provider, scheduler, follow-ups, replies, suppression, negotiation routing.
Phase 5 Verification + Reporting — 1–3 days: verifier, rechecks, funnel, weekly snapshots, ROI, dashboards/export.
Phase 6 Learning + Optimization — 2–4 days: outcomes, features, analyzers, calibration, experiments, proposals, promotion/rollback and UI.
Phase 7 Security/Hardening/Packaging — 3–5 days: security, prompt-injection defense, Electron hardening, performance, installers, migration/upgrade and runbooks.

Calendar estimate with aggressive Claude Code parallelization and engaged human supervision:
- internal MVP: ~7–12 working days
- production-ready v1: ~15–25 working days
- hardened v1 with learning, installers, provider validation and soak testing: ~3–5 calendar weeks

Dependencies that can extend TAT: Claude Code plan limits, local machine performance, OAuth/security setup, legal zero-cost DA/Spam metric source, email-provider restrictions, desktop packaging issues, review turnaround and real-world provider behavior.

Parallel lanes after Phase 0: A Desktop/UI, B Backend/Data, C PydanticAI/Agents, D Discovery/Research/Providers, E QA/Security. Integrate only at phase gates.
