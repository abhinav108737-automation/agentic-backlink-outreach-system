"""Specialist agents (discovery, qualification, research, contact, reply, negotiation)."""

from kb_agents.agents.contact import ContactAgent, ContactTask
from kb_agents.agents.discovery import DiscoveryAgent, DiscoveryTask
from kb_agents.agents.negotiation import NegotiationAgent, NegotiationTask
from kb_agents.agents.qualification import QualificationAgent, QualificationTask
from kb_agents.agents.reply import ReplyAgent, ReplyTask
from kb_agents.agents.research import ResearchAgent, ResearchTask

__all__ = [
    "ContactAgent",
    "ContactTask",
    "DiscoveryAgent",
    "DiscoveryTask",
    "NegotiationAgent",
    "NegotiationTask",
    "QualificationAgent",
    "QualificationTask",
    "ReplyAgent",
    "ReplyTask",
    "ResearchAgent",
    "ResearchTask",
]
