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
