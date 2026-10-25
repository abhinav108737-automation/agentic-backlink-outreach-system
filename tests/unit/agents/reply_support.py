"""Builders for Reply/Negotiation agent tests (fully offline)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agent_support import Rig
from kb_agents.agents.negotiation import NegotiationTask
from kb_agents.agents.reply import DeterministicClass
from kb_contracts.enums import ReplyClass
from kb_contracts.providers import ModelRequest
from kb_core.replies.classify import classify_reply

SCENARIOS = Path(__file__).resolve().parents[3] / "fixtures" / "email" / "scenarios"
THREAD = "thread-1"
RECIPIENT = "editor@example-health.org"

OUTBOUND = {
    "message_id": "out-1",
    "direction": "outbound",
    "subject": "Guest contribution idea",
    "body": "Hello, we would like to propose a guest article on healthcare research.",
}


def deterministic(from_address: str, subject: str, body: str) -> DeterministicClass:
    """Run the REAL deterministic classifier: the agent is only ever handed its decision."""
    outcome = classify_reply(from_address, f"Subject: {subject}\n{body}")
    return DeterministicClass(result=outcome.result, rule=outcome.rule)


def add_thread(
    rig: Rig,
    *,
    subject: str,
    body: str,
    thread_ref: str = THREAD,
    opportunity_id: UUID | None = None,
) -> None:
    rig.world.threads[thread_ref] = {
        "opportunity_id": opportunity_id,
        "domain": "example-health.org",
        "messages": [
            OUTBOUND,
            {
                "message_id": "in-1",
                "direction": "inbound",
                "subject": subject,
                "body": f"Subject: {subject}\n{body}",
                "occurred_at": datetime(2026, 1, 6, tzinfo=UTC),
            },
        ],
    }


def scenario(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SCENARIOS / f"{name}.json").read_text(encoding="utf-8"))
    reply = data["reply"]
    reply["from_address"] = reply["from_address"].replace("{recipient}", RECIPIENT)
    return data


DEFAULT_ACTION: dict[ReplyClass, str] = {
    ReplyClass.OPT_OUT: "no_action",
    ReplyClass.BOUNCE: "no_action",
    ReplyClass.POSITIVE: "draft_reply",
    ReplyClass.QUESTION: "answer_question",
    ReplyClass.NEGOTIATION: "review_terms",
    ReplyClass.NEGATIVE: "no_action",
    ReplyClass.OUT_OF_OFFICE: "wait_until_return",
    ReplyClass.UNKNOWN: "hold_for_human_review",
}


def echo_reply(
    cls: ReplyClass, *, action: str | None = None, confidence: float = 0.9, **extra: Any
) -> dict[str, Any]:
    return {
        "reply_class": cls.value,
        "suggested_action": action or DEFAULT_ACTION[cls],
        "rationale": "Short interpretation.",
        "confidence": confidence,
        **extra,
    }


def negotiation_draft(
    text: str = "Thanks for the details. Could you share your editorial guidelines?",
    *,
    binding: bool = False,
    risk: str = "low",
    recommendation: str = "ask_clarifying_question",
    confidence: float = 0.8,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "draft_response": text,
        "recommendation": recommendation,
        "risk_level": risk,
        "makes_binding_commitment": binding,
        "confidence": confidence,
        **extra,
    }


def new_ids() -> tuple[UUID, UUID]:
    return uuid4(), uuid4()


def prompts_of(requests: list[ModelRequest]) -> str:
    return "\n".join(r.system + "\n" + r.prompt for r in requests)


Responder = Callable[[ModelRequest], dict[str, Any]]


def negotiation_task(
    rig: Rig, name: str = "negotiation-fee", cls: ReplyClass = ReplyClass.NEGOTIATION
) -> NegotiationTask:
    reply = scenario(name)["reply"]
    opportunity = uuid4()
    add_thread(rig, subject=reply["subject"], body=reply["body"], opportunity_id=opportunity)
    return NegotiationTask(
        campaign_id=rig.campaign_id,
        opportunity_id=opportunity,
        thread_ref=THREAD,
        reply_class=cls,
        correlation_id="corr-n",
    )
