"""Deterministic reply classification: ordered rules, first match wins (ADR-0020).

No model is consulted. The input is the stored reply (`from_normalized` + `body_sanitized`, whose
first line is `Subject: ...`) with instruction-like text removed (`authored_text`), so text written
to steer a classifier has no effect on the rule applied, the class or anything persisted.

Rule order (this order IS the precedence; opt-out and bounce can never be overridden by a softer
class because every softer rule sits below them):

  1. bounce.dsn            sender is mailer-daemon/postmaster, or the subject is a delivery-failure
                           notice. Body-only wording never counts: anyone can type "undeliverable".
  2. opt_out.phrase        an explicit request to stop (unsubscribe, remove me, do not contact ...).
  3. out_of_office.auto    out-of-office / automatic-reply markers; extracts an ISO or written
                           return date when one is present.
  4. negotiation.terms     money, fees, sponsorship, link-exchange or link-attribute terms.
  5. negative.decline      a refusal with no terms attached.
  6. positive.interest     expressed interest / invitation to continue.
  7. question.interrogative a question or request for information with no interest marker.
  8. unknown.no_rule       nothing matched: escalate to a human, never guess.

Ordering notes: negotiation sits above negative so "we do not take free posts but charge $250"
opens a negotiation instead of closing a lead; positive sits above question so "interested - what
is your deadline?" is treated as interest. Opt-out sits above out-of-office, so an automatic reply
that carries an unsubscribe request is honoured (the compliance-safe direction).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from kb_contracts.agents import ReplyClassificationResult
from kb_contracts.enums import ReplyClass

from kb_core.outreach.ports import CLASSIFIER_VERSION
from kb_core.replies.sanitize import authored_text

_SUBJECT = re.compile(r"^subject:[ \t]*(.*)$", re.I)
_DSN_SENDERS = re.compile(r"^(mailer-daemon|postmaster|mail-daemon)@", re.I)
_DSN_SUBJECT = re.compile(
    r"undeliver|delivery status notification|mail delivery (failed|subsystem)|returned mail"
    r"|failure notice|delivery (has )?failed|could not be delivered",
    re.I,
)
_OPT_OUT = re.compile(
    r"\bunsubscribe\b|\bopt[- ]?out\b|\bremove (me|us|my)\b|\btake (me|us) off\b"
    r"|\b(do not|don't|dont|never) (contact|email|write to|message) (me|us)\b"
    r"|\bstop (emailing|contacting|writing|sending)\b|\bno (further|more) (emails?|messages?)\b",
    re.I,
)
_OOO = re.compile(
    r"out of (the )?office|automatic reply|auto[- ]?reply|autoreply|\bon (annual )?leave\b"
    r"|\bon vacation\b|\baway (from|until)\b|\bi am away\b|\bwill return on\b",
    re.I,
)
_NEGOTIATION = re.compile(
    r"[$€£]\s?\d|\b\d[\d,.]*\s?(usd|eur|gbp|dollars?|euros?)\b|\bper (post|article|link)\b"
    r"|\b(fee|fees|price|pricing|rates?|rate card|charge|charges|cost|costs|paid)\b"
    r"|\bsponsored\b|\blink exchange\b|\bno-?follow\b|\bdo-?follow\b|\bcounter(-| )?offer\b",
    re.I,
)
_NEGATIVE = re.compile(
    r"\bnot interested\b|\bno,? thanks\b|\bno thank you\b|\bdecline\b|\bnot accepting\b"
    r"|\b(do not|don't|dont|doesn't|does not) (accept|take|publish|run) (any )?(guest|contributed)",
    re.I,
)
_POSITIVE = re.compile(
    r"\binterested\b|\b(happy|glad|pleased) to\b|\bsounds (good|great)\b|\bwe(?:'d| would) "
    r"(love|like)\b|\byes\b|\bgo ahead\b|\bwelcome\b|\bplease send\b|\bsend (me|us|over)\b"
    r"|\blooking forward\b|\bwould be happy\b",
    re.I,
)
_QUESTION = re.compile(
    r"\?|^\s*(who|what|which|how|when|why|could you|can you|do you)\b", re.I | re.M
)

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip
_MONTHS = {name: i for i, name in enumerate(_MONTH_NAMES, start=1)}
_WRITTEN_DATE = re.compile(
    r"\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")|(" + "|".join(_MONTHS)
    + r")\s+(\d{1,2})(?:st|nd|rd|th)?),?\s+(\d{4})\b",
    re.I,
)  # fmt: skip


@dataclass(frozen=True)
class ClassificationOutcome:
    result: ReplyClassificationResult
    rule: str
    return_date: datetime | None
    ignored_instruction_spans: int
    classifier_version: str = CLASSIFIER_VERSION


def _split(body_sanitized: str) -> tuple[str, str]:
    first, _, rest = body_sanitized.partition("\n")
    m = _SUBJECT.match(first)
    return (m.group(1), rest) if m else ("", body_sanitized)


def parse_return_date(text: str, *, after: datetime | None = None) -> datetime | None:
    """First plausible calendar date in `text` as aware UTC midnight, else None."""
    candidates: list[datetime] = []
    for y, mo, d in _ISO_DATE.findall(text):
        candidates.append(_make_date(int(y), int(mo), int(d)))
    for m in _WRITTEN_DATE.finditer(text):
        day = m.group(1) or m.group(4)
        month = (m.group(2) or m.group(3)).lower()
        candidates.append(_make_date(int(m.group(5)), _MONTHS[month], int(day)))
    for when in candidates:
        if when != _INVALID and (after is None or when > after):
            return when
    return None


_INVALID = datetime.min.replace(tzinfo=UTC)


def _make_date(year: int, month: int, day: int) -> datetime:
    try:
        return datetime(year, month, day, tzinfo=UTC)
    except ValueError:
        return _INVALID


def _hit(
    rule: str, cls: ReplyClass, confidence: float, action: str, dropped: int,
    return_date: datetime | None = None,
) -> ClassificationOutcome:  # fmt: skip
    return ClassificationOutcome(
        ReplyClassificationResult(
            reply_class=cls,
            confidence=confidence,
            next_action=action,
            return_date=return_date.isoformat() if return_date else None,
        ),
        rule,
        return_date,
        dropped,
    )


def classify_reply(
    from_normalized: str, body_sanitized: str, *, received_at: datetime | None = None
) -> ClassificationOutcome:
    subject, body = _split(body_sanitized)
    authored_body, dropped = authored_text(body)
    authored_subject, dropped_subject = authored_text(subject)
    dropped += dropped_subject
    text = f"{authored_subject}\n{authored_body}"

    if _DSN_SENDERS.match(from_normalized.strip()) or _DSN_SUBJECT.search(authored_subject):
        return _hit("bounce.dsn", ReplyClass.BOUNCE, 0.99, "suppress_and_stop", dropped)
    if _OPT_OUT.search(text):
        return _hit("opt_out.phrase", ReplyClass.OPT_OUT, 0.97, "suppress_and_stop", dropped)
    if _OOO.search(text):
        when = parse_return_date(text, after=received_at)
        return _hit(
            "out_of_office.auto", ReplyClass.OUT_OF_OFFICE, 0.95, "wait_until_return", dropped, when
        )
    if _NEGOTIATION.search(text):
        return _hit("negotiation.terms", ReplyClass.NEGOTIATION, 0.85, "open_negotiation", dropped)
    if _NEGATIVE.search(text):
        return _hit("negative.decline", ReplyClass.NEGATIVE, 0.9, "close_politely", dropped)
    if _POSITIVE.search(text):
        return _hit("positive.interest", ReplyClass.POSITIVE, 0.8, "open_opportunity", dropped)
    if _QUESTION.search(text):
        return _hit("question.interrogative", ReplyClass.QUESTION, 0.75, "answer_question", dropped)
    return _hit("unknown.no_rule", ReplyClass.UNKNOWN, 0.3, "escalate_to_human", dropped)


RULE_ORDER: tuple[str, ...] = (
    "bounce.dsn",
    "opt_out.phrase",
    "out_of_office.auto",
    "negotiation.terms",
    "negative.decline",
    "positive.interest",
    "question.interrogative",
    "unknown.no_rule",
)
