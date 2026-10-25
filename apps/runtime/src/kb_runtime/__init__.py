"""Composition root: assembles the agent runtime over the deterministic services.

This package exists so that no library package has to depend on a layer above it. The
dependency arrows all point inward:

    kb_runtime ──► kb_agents ──► kb_contracts
         │              │
         │              └──► kb_core.{discovery,research,contacts}   (pure, no db)
         ├──► kb_core  (services, db, repositories)
         ├──► kb_providers  (search, crawler, seo_metrics, model, vector)
         └──► kb_contracts

Enforced by import-linter, not by convention:

* ``kb_core`` must not import ``kb_agents`` — core never depends on the agent layer.
* ``kb_agents`` must not import ``kb_core.db`` or any repository — agents reach persistence
  only through injected ToolGateway capabilities, which is why those capabilities are defined
  *here* rather than inside the agent package.
* ``kb_api`` must not import ``kb_agents``.
* Nothing imports ``kb_runtime``, so it cannot become a back door into either side. There are
  no dynamic imports or service locators used to dodge any of this.

Authorization, scope, argument validation, policy and audit are all enforced on the way
through the gateway, before a capability runs. See ``capabilities`` for what a capability is
allowed to touch and ``composition`` for how the pieces are bound together.
"""

from kb_runtime.capabilities import CapabilityDeps, build_capabilities
from kb_runtime.composition import (
    AgentStack,
    CampaignPolicyGate,
    OutreachStack,
    UowRunStore,
    build_agent_stack,
    build_outreach_stack,
)
from kb_runtime.slice import SliceOutcome, persist_discovery, run_prospect_slice

__all__ = [
    "AgentStack",
    "CampaignPolicyGate",
    "CapabilityDeps",
    "OutreachStack",
    "SliceOutcome",
    "UowRunStore",
    "build_agent_stack",
    "build_capabilities",
    "build_outreach_stack",
    "persist_discovery",
    "run_prospect_slice",
]
