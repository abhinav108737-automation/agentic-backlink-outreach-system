# Canonical Architecture

## 1 Electron Desktop App
Dashboard, campaigns, prospects, outreach/replies, reports, settings, system health. Electron: `contextIsolation=true`, `nodeIntegration=false`, restricted preload/IPC, renderer never accesses DB/secrets directly.

## 2 PydanticAI Agentic Operator
Campaign planning, agent delegation, decisions, drafting, reply interpretation, reporting synthesis, memory access, exception handling, optimization decisions. Not the database, scheduler or policy engine.

## 3 Specialist Agents
Discovery, Qualification, Research, Contact, Outreach, Reply, Negotiation, Backlink Verification, Reporting. Typed inputs/outputs, scoped tools, provenance and deterministic failure routes.

## 4 Deterministic Services
Policy, scheduler, email, SEO metrics, crawling, search, verification, canonicalization/dedupe, audit, rate limiting, suppression.

## 5 Data + Memory
PostgreSQL authoritative; Qdrant semantic/memory only and rebuildable; local file/report storage.

## 6 Learning & Optimization
Outcome Collector, Feature Store, campaign/message/prospect/funnel/backlink/agent analyzers, confidence calibration, pattern discovery, experiments, proposals and promotion gate.

## 7 Optimization Feedback
Controlled updates to ranking weights, thresholds, prospect priorities, search/outreach/subject-line strategies, timing, contact rules, prompts/skills and campaign policies.
