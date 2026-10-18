"""Specialist agents (Wave 1: discovery, qualification, research, contact)."""

from kb_agents.agents.contact import ContactAgent, ContactTask
from kb_agents.agents.discovery import DiscoveryAgent, DiscoveryTask
from kb_agents.agents.qualification import QualificationAgent, QualificationTask
from kb_agents.agents.research import ResearchAgent, ResearchTask

__all__ = [
    "ContactAgent",
    "ContactTask",
    "DiscoveryAgent",
    "DiscoveryTask",
    "QualificationAgent",
    "QualificationTask",
    "ResearchAgent",
    "ResearchTask",
]
