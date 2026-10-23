"""Composition root for the agent runtime (Lead-owned, integration checkpoint I1).

This is the only place that sees both ``kb_agents`` and ``kb_core.db``. ``kb_agents`` may not
import ``kb_core.db`` (import-linter contract 2), which is why the ToolGateway takes injected
callables rather than reaching for repositories itself. That contract also protects this module
automatically: were ``kb_agents`` ever to import ``kb_core.agentic``, it would reach
``kb_core.db`` transitively and the contract would break the build.

Architectural note for Wave 2: ``kb_core`` depending on ``kb_agents`` is an inversion — core
should not depend on agents. It is accepted here because none of the five existing packages can
legally host the wiring (``kb_api`` may not import ``kb_agents`` either), and standing up a
dedicated composition-root package is a Wave-2 decision, not I1 integration.
"""

from kb_core.agentic.capabilities import CapabilityDeps, build_capabilities
from kb_core.agentic.runtime import AgentStack, build_agent_stack
from kb_core.agentic.slice import SliceOutcome, run_prospect_slice

__all__ = [
    "AgentStack",
    "CapabilityDeps",
    "SliceOutcome",
    "build_agent_stack",
    "build_capabilities",
    "run_prospect_slice",
]
