"""C4: typed tool contracts and the per-agent allowlist matrix (SEC-04, ADR-0005)."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from kb_contracts.domain import DTO
from kb_contracts.enums import AgentName


class TypedToolRequest(DTO):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ToolResult(DTO):
    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    policy_blocked: bool = False


# Read/propose-only tools. No agent holds raw DB mutation, shell, direct send, policy override
# or audit deletion. Side-effecting tools only enqueue requests that deterministic services gate.
A = AgentName
TOOL_ALLOWLIST: dict[AgentName, frozenset[str]] = {
    A.OPERATOR: frozenset(
        {
            "read_campaign_state",
            "read_policy",
            "read_memory",
            "read_provider_health",
            "delegate_task",
            "request_send",
            "request_followup",
            "request_verification",
            "request_report",
            "propose_experiment",
            "escalate_to_human",
        }
    ),
    A.DISCOVERY: frozenset({"search", "read_campaign_state", "record_candidates"}),
    A.QUALIFICATION: frozenset({"read_domain_metrics", "read_policy", "record_assessment"}),
    A.RESEARCH: frozenset({"fetch_page", "read_memory", "record_evidence", "record_dossier"}),
    A.CONTACT: frozenset({"fetch_page", "read_evidence", "record_contact_candidates"}),
    A.OUTREACH: frozenset({"read_evidence", "read_memory", "record_draft"}),
    A.REPLY: frozenset({"read_thread", "read_memory", "record_classification"}),
    A.NEGOTIATION: frozenset(
        {"read_thread", "read_policy", "read_memory", "record_negotiation_plan"}
    ),
    A.VERIFICATION: frozenset({"fetch_page", "read_backlink", "record_verification_assessment"}),
    A.REPORTING: frozenset({"read_report_snapshot", "record_narrative"}),
}

FORBIDDEN_CAPABILITIES: frozenset[str] = frozenset(
    {
        "shell",
        "raw_sql",
        "send_email_direct",
        "override_policy",
        "delete_audit",
        "write_status_column",
        "read_secret",
    }
)


def tool_allowed(agent: AgentName, tool: str) -> bool:
    return tool in TOOL_ALLOWLIST.get(agent, frozenset()) and tool not in FORBIDDEN_CAPABILITIES
