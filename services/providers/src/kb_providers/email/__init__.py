"""Email providers. Only the offline mock ships in v1's default configuration."""

from kb_providers.email.mock import (
    MockEmailProvider,
    RecordedDelivery,
    ReplyScenario,
    ScenarioTrigger,
    load_thread,
)

__all__ = [
    "MockEmailProvider",
    "RecordedDelivery",
    "ReplyScenario",
    "ScenarioTrigger",
    "load_thread",
]
