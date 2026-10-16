# Canonical Requirements Registry

Status: canonical (ADR-0013). Baseline is the PRD/TRD v1.0 (FR-01…FR-25, NFR-01…NFR-12); additions FR-26…FR-32 and SEC-01…SEC-08 come from CLAUDE.md, docs and the approved decisions of 2026-10-16.

Rules:
- IDs are stable and unique. An ID is never reused with a different meaning.
- Tests reference IDs with `@pytest.mark.req("FR-09")` (Python) or `// req: FR-09` (TS). A registry check fails on duplicate IDs; rows without tests warn until MVP and fail after.
- Scope: **MVP** = fixture/mock-driven local end-to-end gate; **V1** = integrated release gate (docs/11).
- Lane: owning lane (A UI, B backend, C data, D agents, E providers/outreach, F reporting/learning). Planned tests live under the owning lane's `tests/` subtree unless noted.

## Functional requirements
| ID | Requirement | Source | Scope | Lane |
|---|---|---|---|---|
| FR-01 | Create, edit, pause and archive campaigns with target pages, topics, goals, qualification thresholds, limits, sender identity and follow-up policy | PRD FR-01; docs v0 FR-01 | MVP | B, A |
| FR-02 | Discover candidate websites from configurable search strategies and user-supplied seeds, with provenance | PRD FR-02, §10.1 | MVP | E, D |
| FR-03 | Normalize, deduplicate and canonicalize domains/URLs before expensive research; prevent duplicate contact | PRD FR-03 | MVP | E |
| FR-04 | Collect SEO and quality signals from approved free/local sources through plug-in providers | PRD FR-04 | MVP | E |
| FR-05 | Apply deterministic hard qualification filters incl. configurable DA (> 45) and Spam Score (< 1) thresholds | PRD FR-05, §10.2 | MVP | B |
| FR-06 | Research qualifying websites for topical relevance, editorial quality, contributor policies and suitable pages; store evidence | PRD FR-06 | MVP | E, D |
| FR-07 | Discover and rank candidate contacts with provenance and confidence | PRD FR-07 | MVP | E, D |
| FR-08 | Generate evidence-grounded personalized outreach with a factuality/quality gate and prompt/model/version traceability | PRD FR-08, §11.1 | MVP | D, E |
| FR-09 | Send through a typed email provider (mock default; OAuth/SMTP when configured) with dry-run, idempotency, suppression, rate limits, bounded retries and audit | PRD FR-09 | MVP (mock), V1 (real) | E, B |
| FR-10 | Schedule and execute follow-ups from sequence state, reply state and campaign policy; cancel on reply/opt-out/bounce/block | PRD FR-10 | MVP | B, E |
| FR-11 | Ingest and classify replies (positive, question, negotiation, negative, opt-out, out-of-office, bounce, unknown) and route next action | PRD FR-11, §11.2 | MVP | E, D |
| FR-12 | Negotiation assistance with human takeover and approval when required | PRD FR-12 | MVP | E, D |
| FR-13 | Track agreed guest posts, submitted content, publication status and target URLs | PRD FR-13 | MVP | E |
| FR-14 | Verify backlinks for presence, destination, anchor, status, canonical resolution, rel attributes and persistence | PRD FR-14, §12 | MVP | E |
| FR-15 | Produce weekly executive/operational reports (MVP) and daily operational summaries (V1) | PRD FR-15, §13.1 | MVP / V1 | F |
| FR-16 | Measure funnel conversion from discovery to verified backlink and compute ROI with VERIFIED/ESTIMATED/ASSUMED/TBD labels | PRD FR-16; docs/06 | MVP | F |
| FR-17 | Persist all agent decisions, evidence, tool calls and external actions for auditability | PRD FR-17 | MVP | C, D |
| FR-18 | Learn from historical outcomes (outcomes, features, eval datasets) and generate versioned optimization proposals | PRD FR-18 | MVP | F |
| FR-19 | Run proposals through offline evaluation, shadow/canary/experiment and the promotion gate | PRD FR-19 | MVP (offline + lifecycle), V1 (shadow/canary) | F |
| FR-20 | Electron UI for dashboard, campaigns, prospects, conversations, backlinks, reports, policies, experiments, approvals, agent runs and system health | PRD FR-20 | MVP (core screens) | A |
| FR-21 | Local-model-only operation when no cloud/subscription access exists; end-to-end passes on Ollama | PRD FR-21 | V1 | D |
| FR-22 | Export prospects, activity and reports to CSV/XLSX/PDF-compatible formats | PRD FR-22 | V1 | F |
| FR-23 | Configurable human approval gates for sending, negotiation and other sensitive actions | PRD FR-23; Decision 15 | MVP | B, A |
| FR-24 | Maintain suppression lists and honor opt-out/stop-contact across campaigns | PRD FR-24 | MVP | B |
| FR-25 | Backup, restore and Qdrant rebuild procedures | PRD FR-25 | V1 (rebuild command MVP) | C |
| FR-26 | Explainable opportunity ranking (relevance, authority, editorial fit, traffic, contactability, publication likelihood, history, priority, risk) with versioned weights | PRD §10.2; docs v0 FR-05 | MVP | B, D |
| FR-27 | Autonomy modes shadow/guarded/autonomous and a global kill switch; real send disabled by default and enabled only through an approved activation | PRD §4.1; docs/07; Decision 15 | MVP | B, A |
| FR-28 | Missing required SEO metric ⇒ prospect DEFERRED / not eligible for outreach; never silently passed | Decision 11; PRD §19 | MVP | B |
| FR-29 | Optional GA4/GSC outcome ingestion for SEO reporting; reporting continues without it | PRD §19; Decision 14 | V1 (interface MVP) | F |
| FR-30 | Provider registry with configuration, health, and explicit opt-in for any paid provider | CLAUDE.md; docs/09 | MVP (minimal) | B, A |
| FR-31 | First-run setup: migrations, Qdrant bootstrap, Ollama check, provider setup, self-test, shadow default | PRD §24.2; docs/09 | V1 | A, B |
| FR-32 | Memory service (episodic/semantic/procedural) with provenance; vector store never treated as source of truth | PRD §7.1, §15.3 | MVP | C, D |

## Non-functional requirements
| ID | Requirement | Scope | Lane |
|---|---|---|---|
| NFR-01 | Reliability: idempotent sends and scheduled tasks; bounded retries; recoverable restarts | MVP | B, E |
| NFR-02 | Performance: interactive UI actions < 2 s on local state; background research async; configurable concurrency; target 1,000 prospect evaluations/day where providers permit | V1 | all |
| NFR-03 | Scale: thousands of prospects per campaign, multiple campaigns, no redesign | V1 | C |
| NFR-04 | Offline resilience: local data/reporting usable without internet or model provider | V1 | A, B |
| NFR-05 | Explainability: every qualification, send recommendation and optimization exposes reason, evidence and policy version | MVP | all |
| NFR-06 | Observability: structured logs, metrics, correlation IDs, workflow status, health checks | MVP | B |
| NFR-07 | Security: least privilege, secret isolation, secure Electron bridge, suppression enforcement | MVP | all, Sec |
| NFR-08 | Maintainability: provider interfaces, typed contracts, migrations, ADRs, modular repo | MVP | Lead |
| NFR-09 | Testability: mocks/fixtures for every external provider; CI needs no live credentials | MVP | all, QA |
| NFR-10 | Portability: packaged desktop for macOS first, Windows before production rollout if required; Linux dev build where practical | V1 | A |
| NFR-11 | Accessibility: keyboard navigation, readable contrast, semantic components | V1 | A |
| NFR-12 | Data integrity: PostgreSQL authoritative; Qdrant rebuildable; versioned decisions and policies | MVP | C |

## Security requirements
| ID | Requirement | Source | Scope | Lane |
|---|---|---|---|---|
| SEC-01 | Per-launch authentication between Electron and FastAPI: loopback bind, dynamic port, high-entropy token via secure bootstrap, every privileged request authenticated, token never persisted/logged, restrictive CORS, Host allowlist | Decision 10; ADR-0014 | MVP | B, A |
| SEC-02 | Electron hardening: contextIsolation, sandbox, nodeIntegration off, CSP, minimal typed preload, no renderer secrets | docs/02; docs/07 | MVP | A |
| SEC-03 | Secrets in OS keyring (prod) / env (dev); never in repo, logs, fixtures, prompts or screenshots; log redaction | CLAUDE.md; ADR-0018 | MVP | B |
| SEC-04 | Agents limited to scoped typed tools; no raw DB mutation, shell, unguarded send, policy override or audit deletion | CLAUDE.md; docs/07 | MVP | D, B |
| SEC-05 | Crawled pages and email bodies are untrusted data; prompt-injection defenses and tests | ADR-0020 | MVP | D, E, Sec |
| SEC-06 | Append-only audit history with actor, time, correlation, old/new state and reason | docs v0 FR-19; PRD §20 | MVP | C, B |
| SEC-07 | No consumer AI cookie/session scraping; no authentication bypass; no Moz scraping | CLAUDE.md; Decisions 11–12 | MVP | all |
| SEC-08 | Reference-repository provenance (URL, SHA, license, files studied, adopted/rejected, copied/adapted/reimplemented) | docs/12 | V1 | Lead |

## Traceability from earlier docs/01 (v0) IDs
The v0 file used FR-01…FR-20 with meanings that differ from the PRD. Those IDs are retired; use this map.

| v0 ID | v0 meaning | Canonical |
|---|---|---|
| FR-01 | Campaign management | FR-01 |
| FR-02 | Prospect discovery | FR-02 |
| FR-03 | Deduplication | FR-03 |
| FR-04 | Qualification | FR-04, FR-05, FR-28 |
| FR-05 | Opportunity ranking | FR-26 |
| FR-06 | Website research | FR-06 |
| FR-07 | Contact discovery | FR-07 |
| FR-08 | Personalization | FR-08 |
| FR-09 | Outreach execution | FR-09, FR-23, FR-24 |
| FR-10 | Follow-up scheduling | FR-10 |
| FR-11 | Reply ingestion & classification | FR-11 |
| FR-12 | Negotiation support | FR-12, FR-23 |
| FR-13 | Backlink verification | FR-14 |
| FR-14 | Weekly reporting | FR-15, FR-16 |
| FR-15 | Learning | FR-18 |
| FR-16 | Optimization | FR-18 |
| FR-17 | Promotion governance | FR-19 |
| FR-18 | Explainability | NFR-05 |
| FR-19 | Audit | FR-17, SEC-06 |
| FR-20 | Operations UI | FR-20 |
