"""C1: enums shared across all lanes. Values are stable wire/DB strings."""

from enum import StrEnum


class AutonomyMode(StrEnum):
    SHADOW = "shadow"
    GUARDED = "guarded"
    AUTONOMOUS = "autonomous"


class RiskClass(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class CampaignStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class ProspectStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    DEFERRED = "DEFERRED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    RANKED = "RANKED"
    RESEARCHED = "RESEARCHED"
    CONTACT_READY = "CONTACT_READY"
    OUTREACH_READY = "OUTREACH_READY"
    IN_OUTREACH = "IN_OUTREACH"
    WON = "WON"
    LOST = "LOST"
    SUPPRESSED = "SUPPRESSED"


class SequenceStatus(StrEnum):
    PLANNED = "PLANNED"
    READY = "READY"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    WAITING_REPLY = "WAITING_REPLY"
    FOLLOW_UP_DUE = "FOLLOW_UP_DUE"
    COMPLETED_NO_REPLY = "COMPLETED_NO_REPLY"
    REPLIED = "REPLIED"
    OPTED_OUT = "OPTED_OUT"
    BOUNCED = "BOUNCED"
    CANCELLED = "CANCELLED"


class MessageStatus(StrEnum):
    DRAFT = "DRAFT"
    QA_FAILED = "QA_FAILED"
    QA_PASSED = "QA_PASSED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    RETRY_WAIT = "RETRY_WAIT"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    BOUNCED = "BOUNCED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OpportunityStatus(StrEnum):
    OPEN = "OPEN"
    NEGOTIATING = "NEGOTIATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    ESCALATED_TO_HUMAN = "ESCALATED_TO_HUMAN"
    AGREED = "AGREED"
    CONTENT_IN_PROGRESS = "CONTENT_IN_PROGRESS"
    CONTENT_SUBMITTED = "CONTENT_SUBMITTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    PUBLISHED = "PUBLISHED"
    LOST = "LOST"


class BacklinkStatus(StrEnum):
    EXPECTED = "EXPECTED"
    FOUND = "FOUND"
    VERIFIED = "VERIFIED"
    CHANGED = "CHANGED"
    MISSING = "MISSING"
    LOST = "LOST"


class OptimizationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    OFFLINE_TEST = "OFFLINE_TEST"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    EXPERIMENT = "EXPERIMENT"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    MONITORED = "MONITORED"
    ROLLED_BACK = "ROLLED_BACK"


class WorkflowStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    DEAD = "DEAD"
    CANCELLED = "CANCELLED"


class QualificationOutcome(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


class ReplyClass(StrEnum):
    POSITIVE = "positive"
    QUESTION = "question"
    NEGOTIATION = "negotiation"
    NEGATIVE = "negative"
    OPT_OUT = "opt_out"
    OUT_OF_OFFICE = "out_of_office"
    BOUNCE = "bounce"
    UNKNOWN = "unknown"


class SuppressionScope(StrEnum):
    EMAIL = "email"
    DOMAIN = "domain"
    CONTACT = "contact"


class SuppressionReason(StrEnum):
    OPT_OUT = "opt_out"
    BOUNCE = "bounce"
    COMPLAINT = "complaint"
    MANUAL = "manual"
    LEGAL = "legal"


class ApprovalKind(StrEnum):
    LIVE_SEND_ACTIVATION = "live_send_activation"
    L3_POLICY_CHANGE = "l3_policy_change"
    NEGOTIATION_COMMITMENT = "negotiation_commitment"
    MESSAGE_SEND = "message_send"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OperatorAction(StrEnum):
    DELEGATE = "DELEGATE"
    SEND = "SEND"
    WAIT = "WAIT"
    FOLLOW_UP = "FOLLOW_UP"
    ESCALATE = "ESCALATE"
    STOP = "STOP"
    VERIFY = "VERIFY"
    REPORT = "REPORT"
    EXPERIMENT = "EXPERIMENT"


class DeliveryMode(StrEnum):
    MOCK = "mock"
    REAL = "real"


class ActorType(StrEnum):
    OPERATOR = "operator"
    AGENT = "agent"
    SYSTEM = "system"


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


class ValueLabel(StrEnum):
    """ROI/report value provenance label (docs/06)."""

    VERIFIED = "VERIFIED"
    ESTIMATED = "ESTIMATED"
    ASSUMED = "ASSUMED"
    TBD = "TBD"


class AgentName(StrEnum):
    OPERATOR = "operator"
    DISCOVERY = "discovery"
    QUALIFICATION = "qualification"
    RESEARCH = "research"
    CONTACT = "contact"
    OUTREACH = "outreach"
    REPLY = "reply"
    NEGOTIATION = "negotiation"
    VERIFICATION = "verification"
    REPORTING = "reporting"
