# Shared Contracts and Provider Interfaces

Status: canonical (ADR-0016). Contracts v0.1 are **frozen at the Foundation Gate**. Only the Level-0 Lead changes them.

## Provider interfaces
Protocols live in `kb_contracts.providers`. Every provider exposes `name`, `is_paid: bool` and `health() -> ProviderHealth`, raises only the shared error taxonomy (`ProviderUnavailable`, `RateLimited`, `AuthRequired`, `InvalidResponse`, `NotConfigured`), and every implementation passes one shared fixture-based contract suite.

| Protocol | MVP implementations | V1 / later | Owner |
|---|---|---|---|
| ModelProvider | Ollama (`qwen3:8b-q4_K_M`, configurable); Fake/TestModel for CI | officially supported authenticated providers only; never consumer sessions (ADR-0004) | D |
| EmbeddingProvider | Ollama (`nomic-embed-text`, configurable); deterministic Fake | — | D |
| VectorProvider | Qdrant; InMemory | — | C |
| SearchProvider | Mock (fixtures); ManualSeed (user-supplied domains) | SearXNG over HTTP (AGPL: run as a separate service, no code copying) | E |
| CrawlerProvider | Mock (fixture HTML); robots-aware httpx crawler | Crawl4AI pilot | E |
| BrowserProvider | Null (disabled) | Playwright fallback after deterministic crawl; approved public pages only | E |
| SEOMetricsProvider | Mock; ManualImport (CSV) | real provider TBD; no Moz scraping or auth bypass (ADR-0015) | E |
| EmailProvider (send + receive) | Mock with reply-simulation scenarios | Gmail OAuth / IMAP+SMTP, disabled by default | E |
| AnalyticsSourceProvider (GA4/GSC) | interface + Null | implementations after the core vertical slice | F |
| ReportExporter | JSON, CSV | XLSX, PDF | F |
| SecretStore | Env (dev) | OS keyring via cross-platform `keyring` | B |
| JobQueue / DurableWorkflowProvider | PostgresJobQueue | DBOS only if the spike justifies it (ADR-0008) | B |
| Clock | SystemClock; FakeClock for deterministic timer tests | — | B |

Internal services (not providers) built on these: MemoryService, PolicyEngine, VerificationService, AnalyticsService, ExperimentService.

## Frozen shared contracts (v0.1)
| ID | Contract | Location |
|---|---|---|
| C1 | Enums and state-machine transition tables (docs/13) | `kb_contracts.state_machines` |
| C2 | Pydantic domain DTOs for every aggregate in docs/04, incl. the PolicyVersion body schema | `kb_contracts.domain` |
| C3 | Agent I/O: `OperatorDecision` (below), `TaskPlan`, `DiscoveryResult`, `QualificationAssessment`, `DossierDraft`, `ContactCandidates`, `DraftMessage` (claims → evidence_ids), `ReplyClassificationResult`, `NegotiationPlan`, `VerificationAmbiguityResult`, `WeeklyNarrative`, `AgentRunRecord` | `kb_contracts.agents` |
| C4 | Tool contracts: `TypedToolRequest`, `ToolResult`, per-agent tool allowlist matrix | `kb_contracts.tools` |
| C5 | Provider protocols + error taxonomy (above) | `kb_contracts.providers` |
| C6 | Policy gate: `PolicyCheckRequest → PolicyDecision {allowed, block_codes, requires_approval, policy_version_id}`. Block codes: SUPPRESSED, CAMPAIGN_PAUSED, HARD_THRESHOLD_FAILED, METRIC_MISSING, UNSUPPORTED_CLAIM, DUPLICATE_CONTACT, DUPLICATE_STEP, SEND_LIMIT_EXCEEDED, SENDER_INVALID, UNSAFE_BROWSER_ACTION, PROVIDER_AUTH_UNAVAILABLE, APPROVAL_REQUIRED, KILL_SWITCH_ACTIVE, REAL_SEND_DISABLED | `kb_contracts.policy` |
| C7 | Domain event + audit envelope: event_id, type, occurred_at, correlation_id, causation_id, actor, subject, old_state, new_state, reason, payload | `kb_contracts.events` |
| C8 | HTTP API v0: PRD §22 routes plus `/auth/bootstrap` (internal), `/health/live`, `/health/ready`, approvals, settings, providers, agent runs. Errors `application/problem+json`; headers `Authorization: Bearer`, `Idempotency-Key`, `X-Correlation-ID`; cursor pagination | `apps/api` + `contracts/openapi/openapi.json` |
| C9 | Generated TS client `@ken/contracts`; CI drift check against the committed OpenAPI snapshot | `contracts/generated_ts` |
| C10 | Electron bridge: preload exposes `kenApi.request()`, `kenApi.backendStatus()`, `kenApi.onBackendStatus()`; backend handshake = token over stdin, backend prints `READY port=<n>` on stdout (ADR-0014) | `apps/desktop` |
| C11 | Config schema (typed Settings; `.env.example` extended), DB naming conventions, migration 001 baseline | `kb_contracts.config`, `migrations/` |
| C12 | Fixture formats: SERP result, HTML site bundle, metrics CSV, email thread, reply scenario | `fixtures/README.md` |
| C13 | Structured log schema and redaction list | `kb_contracts.logging` |

### OperatorDecision (PRD §7.2)
```
OperatorDecision {
  decision_id: UUID
  campaign_id: UUID
  action: DELEGATE | SEND | WAIT | FOLLOW_UP | ESCALATE | STOP | VERIFY | REPORT | EXPERIMENT
  subject_id: UUID | null
  rationale: str
  evidence_ids: list[UUID]
  confidence: float [0..1]
  policy_version: str
  tool_request: TypedToolRequest | null
  requires_human_approval: bool
}
```
`SEND` is a request, never an execution: it goes through the policy gate and EmailService.

## Contract change process
1. A lane files a Contract Change Request (CCR) with the Lead: what, why, affected lanes.
2. The Lead updates `contracts/`, bumps the contract version, adds a `contracts/CHANGELOG.md` entry, regenerates OpenAPI + TS.
3. Breaking changes require an ADR note. Lanes pick up the change at the next integration checkpoint.
4. Lanes never edit `contracts/**` or `contracts/generated_ts/**` directly.
