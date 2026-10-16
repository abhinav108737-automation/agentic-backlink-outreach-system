"""Route registry. Pre-registered at the contract freeze so lanes never edit this file."""

from types import ModuleType

from kb_api.routes import (
    agent_runs,
    approvals,
    backlinks,
    campaigns,
    experiments,
    messages,
    optimization_proposals,
    prospects,
    providers,
    reports,
    settings,
    system,
    threads,
)

ROUTER_MODULES: tuple[ModuleType, ...] = (
    campaigns,
    prospects,
    messages,
    threads,
    backlinks,
    reports,
    experiments,
    optimization_proposals,
    approvals,
    settings,
    providers,
    agent_runs,
    system,
)
