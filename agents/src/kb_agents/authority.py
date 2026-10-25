"""Where deterministic authority bounds the Reply and Negotiation agents (ADR-0020, SEC-04).

``kb_core.replies.classify.classify_reply`` and ``kb_core.negotiation.service.needs_human`` are
the safety authorities. ``kb_agents`` may not import them (both reach ``kb_core.db`` through
``kb_core.outreach.ports`` / ``kb_core.db.protocols``, which the import boundary forbids), so
this module holds the agent-side half of the contract:

* the closed vocabulary of *suggestions* an agent may return (none of which suppresses,
  approves, sends or moves a state machine) and which suggestions are legal per reply class;
* a mirror of the commitment detector that can only ever RAISE ``makes_binding_commitment``;
  ``tests/unit/agents/test_authority.py`` pins it to ``kb_core`` so it cannot drift;
* the text view of a thread a model may see: untrusted, instruction-like spans removed.
"""

from __future__ import annotations

import re
from typing import Literal, get_args

from kb_contracts.enums import ReplyClass
from kb_core.replies.sanitize import authored_text
from kb_core.research.untrusted import looks_like_instruction

# ------------------------------------------------------------------------------ reply

SuggestedReplyAction = Literal[
    "no_action",
    "draft_reply",
    "answer_question",
    "review_terms",
    "wait_until_return",
    "hold_for_human_review",
]
SUGGESTED_REPLY_ACTIONS: frozenset[str] = frozenset(get_args(SuggestedReplyAction))

# The deterministic classifier owns these; the agent has no say and no useful action to suggest.
PROTECTED_CLASSES: frozenset[ReplyClass] = frozenset({ReplyClass.OPT_OUT, ReplyClass.BOUNCE})

ALLOWED_ACTIONS_BY_CLASS: dict[ReplyClass, frozenset[str]] = {
    ReplyClass.OPT_OUT: frozenset({"no_action"}),
    ReplyClass.BOUNCE: frozenset({"no_action"}),
    ReplyClass.POSITIVE: frozenset({"draft_reply", "hold_for_human_review", "no_action"}),
    ReplyClass.QUESTION: frozenset({"answer_question", "draft_reply", "hold_for_human_review"}),
    ReplyClass.NEGOTIATION: frozenset({"review_terms", "hold_for_human_review"}),
    ReplyClass.NEGATIVE: frozenset({"no_action", "hold_for_human_review"}),
    ReplyClass.OUT_OF_OFFICE: frozenset({"wait_until_return", "no_action"}),
    ReplyClass.UNKNOWN: frozenset({"hold_for_human_review", "no_action"}),
}

# Classes a negotiation round may be planned for. Everything else is closed or suppressed.
NEGOTIABLE_CLASSES: frozenset[ReplyClass] = frozenset(
    {ReplyClass.NEGOTIATION, ReplyClass.POSITIVE, ReplyClass.QUESTION}
)

# ------------------------------------------------------------------------- negotiation

NegotiationRecommendation = Literal[
    "reply_with_draft",
    "ask_clarifying_question",
    "propose_counter_for_review",
    "hold_for_human_review",
    "escalate_to_human",
]
RiskLevel = Literal["low", "medium", "high"]

# Mirror of kb_core.negotiation.service._COMMITMENT (pinned by a parity test). The agent ORs this
# with the injected deterministic check; either one alone is enough to raise the flag.
_COMMITMENT = re.compile(
    r"[$€£]\s?\d|\b\d[\d,.]*\s?(usd|eur|gbp|dollars?|euros?)\b"
    r"|\bwe (agree|accept|confirm|commit|will pay|will publish|guarantee)\b"
    r"|\bi (agree|accept|confirm|commit)\b|\bdeal\b|\bguarantee[sd]?\b|\bbinding\b"
    r"|\bpay(ing|ment|ments)?\b|\bcontract\b|\bexclusive(ly)?\b|\binvoice\b",
    re.I,
)
COMMITMENT_PATTERN = _COMMITMENT.pattern


def detect_commitment(text: str) -> bool:
    return bool(_COMMITMENT.search(text))


# ------------------------------------------------------------------------- untrusted text

_SUBJECT_LINE = re.compile(r"^subject:[ \t]*(.*)$", re.I)


def split_subject(body: str) -> tuple[str, str]:
    """Split the ``Subject: ...`` first line that stored reply bodies carry."""
    first, _, rest = body.partition("\n")
    match = _SUBJECT_LINE.match(first)
    return (match.group(1), rest) if match else ("", body)


def model_safe_text(text: str, *, max_chars: int) -> tuple[str, int]:
    """Text a model may see: instruction-like lines/sentences removed, capped.

    Returns ``(text, dropped_segments)``. Removal reuses the deterministic ``authored_text`` the
    classifier itself reads, then drops any surviving line the research detector flags.
    """
    authored, dropped = authored_text(text)
    kept: list[str] = []
    for line in authored.splitlines():
        if looks_like_instruction(line):
            dropped += 1
        else:
            kept.append(line)
    return "\n".join(kept).strip()[:max_chars], dropped
