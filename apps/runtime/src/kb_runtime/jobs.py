"""The typed job registry: the only place a job type becomes executable code.

Why it is shaped this way:

* A registry entry is built by binding a **frozen job-type constant** from
  ``kb_core.scheduling.jobs`` to a closure that already holds its dependencies. Nothing is
  resolved at dispatch time.
* **No callable name, module path, attribute or import ever comes from a job payload.** A
  payload carries identifiers and small scalars, validated by the frozen Pydantic payload
  models (``extra="forbid"``). There is no ``getattr``, no ``importlib``, no ``eval`` and no
  service locator anywhere on this path — so a hostile or malformed payload can at worst fail
  validation, never choose what runs.
* An unknown job type is **dead-lettered by the worker**, not looked up dynamically.
* ``build_registry`` returns every type in ``ALL_JOB_TYPES`` or raises. A type added to the
  contract but left unbound is a startup error rather than a job that silently dead-letters in
  production.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from kb_contracts.providers import Clock, CrawlerProvider, EmailProvider
from kb_core.negotiation.handlers import make_negotiation_handler
from kb_core.outreach.handlers import make_draft_handler, make_follow_up_handler, make_send_handler
from kb_core.replies.handlers import make_classify_handler, make_ingest_handler
from kb_core.scheduling.jobs import (
    ALL_JOB_TYPES,
    BACKLINK_VERIFY,
    NEGOTIATION_PLAN,
    OUTREACH_DRAFT,
    OUTREACH_FOLLOW_UP,
    OUTREACH_SEND,
    REPLIES_CLASSIFY,
    REPLIES_INGEST,
)
from kb_core.scheduling.worker import JobHandler
from kb_core.verification.handlers import make_verify_handler


@dataclass(frozen=True)
class WorkerDeps:
    """Everything the handlers need, injected. No handler reaches for a global."""

    uow_factory: Any
    transitions: Any
    policy: Any
    clock: Clock
    email: EmailProvider
    crawler: CrawlerProvider
    send_gate: Any
    draft_producer: Any
    plan_producer: Any
    lost_after_misses: int = 3
    recheck_every: timedelta | None = None


def build_registry(deps: WorkerDeps) -> Mapping[str, JobHandler]:
    """Bind every frozen job-type constant to a pre-constructed handler."""
    registry: dict[str, JobHandler] = {
        OUTREACH_DRAFT: make_draft_handler(deps.uow_factory, deps.transitions, deps.draft_producer),
        OUTREACH_SEND: make_send_handler(
            deps.uow_factory, deps.email, deps.send_gate, deps.transitions, deps.clock
        ),
        OUTREACH_FOLLOW_UP: make_follow_up_handler(
            deps.uow_factory, deps.transitions, deps.policy, deps.clock
        ),
        REPLIES_INGEST: make_ingest_handler(deps.uow_factory, deps.email, deps.transitions),
        REPLIES_CLASSIFY: make_classify_handler(deps.uow_factory, deps.transitions),
        NEGOTIATION_PLAN: make_negotiation_handler(
            deps.uow_factory, deps.transitions, deps.plan_producer
        ),
        BACKLINK_VERIFY: make_verify_handler(
            deps.uow_factory,
            deps.crawler,
            deps.transitions,
            deps.clock,
            lost_after_misses=deps.lost_after_misses,
            recheck_every=deps.recheck_every,
        ),
    }
    missing = set(ALL_JOB_TYPES) - set(registry)
    if missing:  # a contract job type with no handler is a startup error, not a silent gap
        raise RuntimeError(f"job types declared but not bound: {sorted(missing)}")
    unexpected = set(registry) - set(ALL_JOB_TYPES)
    if unexpected:  # and a handler for a type the contract does not declare is equally wrong
        raise RuntimeError(f"handlers bound for undeclared job types: {sorted(unexpected)}")
    return registry


__all__ = ["WorkerDeps", "build_registry"]
