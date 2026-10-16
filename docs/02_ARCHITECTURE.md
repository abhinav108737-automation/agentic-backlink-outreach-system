# Canonical Architecture

Status: canonical (reconciled 2026-10-16, see ADR-0009). Canonical diagram: `reference/architecture_reference.png`.
The Internal Linking diagram under `docs/reference/methodology/` is **not** this project's architecture.

## Layers

```
L1 Electron Desktop App
   Screens: Dashboard, Campaigns, Prospects, Outreach, Replies, Backlinks, Reports,
            Experiments, Policies, Approvals, Agent Runs, Settings, Health.
   main     : window, backend supervisor, per-launch token, IPC proxy to backend
   preload  : minimal contextBridge (kenApi.*); never exposes the token
   renderer : React + TypeScript strict; no Node/Electron privileged APIs
        │  IPC → main → HTTP 127.0.0.1:<dynamic port>, Authorization: Bearer <per-launch token>
L2 FastAPI control plane
   thin routes · auth · correlation-ID middleware · problem+json errors · OpenAPI → typed TS client
        │
L3 PydanticAI Agentic Operator
   campaign planning, delegation, decisions, drafting, reply interpretation, reporting synthesis,
   memory access, exception handling, optimization decisions → typed OperatorDecision.
   NOT the database, scheduler or policy engine. Never mutates protected state directly.
        │  typed contracts
L4 Specialist agents (9)
   Discovery, Qualification, Research, Contact, Outreach, Reply, Negotiation,
   Backlink Verification, Reporting
        │  ToolGateway: per-agent tool allowlist, policy pre-check, audit, tool_calls record
L5 Deterministic services
   PolicyEngine (hard eligibility, send rules, approvals, limits), Suppression, Idempotency,
   Scheduler/JobQueue (PostgreSQL-backed), Workflow runner, Audit, Canonicalization/Dedupe,
   Search, Crawler, SEO metrics, Email, Verification, Memory, Analytics, Experiments
        │  provider protocols (docs/14)
L6 Data + Memory
   PostgreSQL — authoritative system of record
   Qdrant     — derived semantic memory; fully rebuildable from PostgreSQL
   Local file store — reports/exports
L7 Learning & Optimization
   Outcome Collector → Feature snapshots → Campaign/Message/Prospect/Funnel/Backlink/Agent
   analyzers → Confidence calibration → Pattern discovery → Experiment engine →
   Optimization proposals → Evaluation/Promotion gate
L8 Optimization feedback
   Governed new policy_versions / prompt-skill versions (ranking weights, thresholds, priorities,
   search/outreach/subject-line strategies, follow-up timing, contact rules, prompts/skills,
   campaign policies) → read by the Operator
```

## Runtime decisions
| Concern | Decision | ADR |
|---|---|---|
| Desktop | Electron + React + TS; electron-vite build; electron-builder packaging | 0001 |
| Agent framework | PydanticAI is the runtime Operator; no LangGraph | 0002 |
| Data | PostgreSQL authoritative; Qdrant derived/rebuildable | 0003 |
| Models | Ollama local baseline; zero paid API; no consumer-subscription access in v1. Initial spike models `qwen3:8b-q4_K_M` (reasoning) and `nomic-embed-text` (embeddings), configurable, evaluated before becoming defaults. CI uses PydanticAI TestModel/FunctionModel. | 0004 |
| Side effects | Deterministic services own every external side effect; agents use ToolGateway | 0005 |
| Learning | Gated promotion lifecycle; risk classes L1/L2/L3 | 0006 |
| Autonomy | shadow (dev default) → guarded (first real-email release) → autonomous (explicit); global kill switch; real send off by default | 0007 |
| Scheduling | PostgreSQL-backed job queue (`scheduled_jobs`, `FOR UPDATE SKIP LOCKED`, leases, bounded retries, idempotency and concurrency keys). DBOS evaluated in a time-boxed spike. | 0008 |
| Localhost security | Loopback bind, dynamic port, per-launch token via stdin handshake, IPC proxy, deny-all CORS, Host allowlist | 0014 |
| SEO metrics | Provider abstraction; missing required metric ⇒ DEFERRED, never pass | 0015 |
| Infrastructure | No Redis, Celery, LangGraph, Elasticsearch, S3, Kubernetes without ADR | 0017 |
| Secrets | Env in dev; OS keyring (cross-platform) in production; redaction everywhere | 0018 |
| Packaging | macOS first smoke target; architecture stays cross-platform; Windows before production rollout if users are on Windows | 0019 |
| Untrusted content | Crawled pages and email bodies are data, never instructions | 0020 |

## Process topology
- **Development:** Docker Compose runs PostgreSQL (host port 55432) and Qdrant (56333). Ollama runs on the host. Electron main spawns the FastAPI backend; the job worker runs in-process as an asyncio task.
- **Production:** packaged Electron app supervises the bundled backend. How PostgreSQL, Qdrant and the Python backend are distributed is decided by the packaging spike (ADR-0019, Proposed). Docker is a development dependency only.

## Electron security baseline
`contextIsolation=true`, `nodeIntegration=false`, `sandbox=true`, strict CSP, no remote content, minimal typed preload, renderer never holds secrets or the API token, no direct DB access.
