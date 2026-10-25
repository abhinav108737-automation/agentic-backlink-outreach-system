"""Render a thread for a model: every message untrusted, instruction-like spans removed."""

from __future__ import annotations

from dataclasses import dataclass

from kb_agents.authority import model_safe_text, split_subject
from kb_agents.prompts.common import wrap_untrusted
from kb_agents.runs.lifecycle import AgentFailure
from kb_agents.tools.specs import ThreadMessageView, ThreadView

MAX_CONTEXT_MESSAGES = 4
FOCUS_CHARS = 4000
CONTEXT_CHARS = 1000


@dataclass(frozen=True)
class RenderedThread:
    text: str
    focus: ThreadMessageView
    dropped_segments: int


def pick_focus(thread: ThreadView, reply_id: str | None) -> ThreadMessageView:
    """The inbound message under analysis: ``reply_id`` if given, else the latest inbound."""
    inbound = [m for m in thread.messages if m.direction == "inbound"]
    if reply_id is not None:
        for message in inbound:
            if message.message_id == reply_id:
                return message
        raise AgentFailure("reply_not_in_thread", "the named reply is not an inbound message")
    if not inbound:
        raise AgentFailure("no_inbound_message", "the thread has no inbound reply")
    return inbound[-1]


def render_thread(thread: ThreadView, focus: ThreadMessageView) -> RenderedThread:
    upto = list(thread.messages)
    index = next(i for i, m in enumerate(upto) if m.message_id == focus.message_id)
    window = upto[max(0, index - (MAX_CONTEXT_MESSAGES - 1)) : index + 1]
    blocks: list[str] = []
    dropped_total = 0
    for position, message in enumerate(window, start=1):
        stored_subject, body = split_subject(message.body)
        subject = message.subject or stored_subject
        limit = FOCUS_CHARS if message is focus else CONTEXT_CHARS
        safe_body, dropped_body = model_safe_text(body, max_chars=limit)
        safe_subject, dropped_subject = model_safe_text(subject, max_chars=200)
        dropped_total += dropped_body + dropped_subject
        origin = f"{message.direction} email" + (" (under analysis)" if message is focus else "")
        blocks.append(
            wrap_untrusted(f"M{position}", origin, f"Subject: {safe_subject}\n{safe_body}")
        )
    return RenderedThread(text="\n".join(blocks), focus=focus, dropped_segments=dropped_total)
