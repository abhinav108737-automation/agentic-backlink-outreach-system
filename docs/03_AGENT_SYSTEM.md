# Agent System

## Operator
Inputs: campaign state, pending work, policy, memory summary, provider health, optimization proposals.
Outputs: typed task plan, delegation, decisions, next action, exception/escalation, report synthesis and optimization decisions.

## Specialist agents
Discovery — candidate domains/URLs with provenance.
Qualification — eligibility evidence + opportunity scoring; hard policy is deterministic.
Research — website evidence, topics, editorial/contact patterns.
Contact — best contact candidates + confidence/provenance.
Outreach — evidence-grounded drafts; never sends directly.
Reply — typed intent/status + next action.
Negotiation — draft/recommendation; no binding commitment unless policy permits.
Verification — deterministic checks first, agent for ambiguity.
Reporting — narrative synthesis from deterministic metrics only.

## Persist each agent run
agent_run_id, campaign_id, task type, input references, model/provider, prompt/skill version, tool calls, structured result, confidence, policy version, latency, failure/fallback and timestamps.

Persisted in `agent_runs` (model calls are part of the run; there is no separate `model_runs` table), `tool_calls` and `operator_decisions` (docs/04).

## Operator decision contract
The Operator returns a typed `OperatorDecision` (PRD §7.2; frozen contract C3 in docs/14). Actions: DELEGATE, SEND, WAIT, FOLLOW_UP, ESCALATE, STOP, VERIFY, REPORT, EXPERIMENT. `SEND` is a request routed through the deterministic policy gate and EmailService; it never executes a send itself.

## Tool access
Agents act only through the ToolGateway, which enforces a per-agent tool allowlist (contract C4), runs the policy pre-check, records `tool_calls` and writes audit events. Agents never set lifecycle status columns; deterministic services perform transitions (docs/13). Crawled pages and email bodies reach agents only as quoted, untrusted evidence (ADR-0020).

## Runtime models
Ollama is the default runtime (ADR-0004). Initial spike models: `qwen3:8b-q4_K_M` (reasoning/structured output) and `nomic-embed-text` (embeddings). Names are configurable and neither becomes the production default before an evaluation. CI uses PydanticAI TestModel/FunctionModel.
