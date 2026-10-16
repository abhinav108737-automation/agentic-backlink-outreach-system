import json
import logging
from uuid import uuid4

import pytest
from kb_contracts import CONTRACT_VERSION
from kb_contracts.agents import DraftClaim, DraftMessage, OperatorDecision
from kb_contracts.config import Settings
from kb_contracts.domain import PolicyBody
from kb_contracts.enums import AgentName, OperatorAction
from kb_contracts.logging import REDACTED, REDACTOR, JsonFormatter, Redactor
from kb_contracts.policy import BlockCode
from kb_contracts.tools import FORBIDDEN_CAPABILITIES, TOOL_ALLOWLIST, tool_allowed
from pydantic import ValidationError


def test_version():
    assert CONTRACT_VERSION == "0.1.0"


@pytest.mark.req("FR-27")
def test_safe_defaults(monkeypatch):
    for k in ("REAL_SEND_ENABLED", "AUTONOMY_MODE", "EMAIL_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.real_send_enabled is False
    assert s.autonomy_mode.value == "shadow"
    assert s.email_provider == "mock" and s.search_provider == "mock"
    assert s.qdrant_url.endswith(":56333")


@pytest.mark.req("FR-05")
def test_policy_defaults_match_initial_policy_and_never_pass_on_missing():
    p = PolicyBody()
    assert (p.min_da, p.max_spam) == (45.0, 1.0)
    assert p.on_missing_metric == "defer"
    with pytest.raises(ValidationError):
        PolicyBody(on_missing_metric="pass")


@pytest.mark.req("SEC-04")
def test_no_agent_has_a_forbidden_capability_and_operator_cannot_send_directly():
    for agent, tools in TOOL_ALLOWLIST.items():
        assert not tools & FORBIDDEN_CAPABILITIES, agent
    assert set(TOOL_ALLOWLIST) == set(AgentName)
    assert not tool_allowed(AgentName.OUTREACH, "send_email_direct")
    assert not tool_allowed(AgentName.OUTREACH, "request_send")  # operator-only request
    assert tool_allowed(AgentName.OPERATOR, "request_send")


@pytest.mark.req("FR-08")
def test_draft_claim_requires_evidence():
    with pytest.raises(ValidationError):
        DraftClaim(text="They won an award", evidence_ids=())
    DraftClaim(text="ok", evidence_ids=(uuid4(),))
    DraftMessage(sequence_id=uuid4(), step_no=1, subject="s", body="b")


def test_operator_decision_validates_confidence():
    kw = dict(
        decision_id=uuid4(),
        campaign_id=uuid4(),
        action=OperatorAction.WAIT,
        rationale="r",
        policy_version="1",
    )
    OperatorDecision(confidence=0.5, **kw)
    with pytest.raises(ValidationError):
        OperatorDecision(confidence=1.5, **kw)


def test_block_codes_cover_prd_hard_blocks_plus_metric_missing():
    needed = {
        "SUPPRESSED",
        "CAMPAIGN_PAUSED",
        "HARD_THRESHOLD_FAILED",
        "METRIC_MISSING",
        "UNSUPPORTED_CLAIM",
        "DUPLICATE_CONTACT",
        "DUPLICATE_STEP",
        "SEND_LIMIT_EXCEEDED",
        "SENDER_INVALID",
        "UNSAFE_BROWSER_ACTION",
        "PROVIDER_AUTH_UNAVAILABLE",
        "APPROVAL_REQUIRED",
        "KILL_SWITCH_ACTIVE",
        "REAL_SEND_DISABLED",
    }
    assert needed == {c.value for c in BlockCode}


@pytest.mark.req("SEC-03")
def test_redaction_scrubs_keys_bearer_and_registered_literals():
    r = Redactor()
    r.register_secret("s3cr3t-launch-token-value")
    out = r.scrub(
        {
            "Authorization": "Bearer abc.def",
            "n": {"password": "x"},
            "m": "see s3cr3t-launch-token-value and Bearer zzz111 token=hunter22",
        }
    )
    assert out["Authorization"] == REDACTED and out["n"]["password"] == REDACTED
    assert "s3cr3t-launch-token-value" not in out["m"] and "zzz111" not in out["m"]
    assert "hunter22" not in out["m"]


@pytest.mark.req("SEC-03")
def test_json_formatter_emits_one_redacted_line():
    REDACTOR.register_secret("another-long-secret-123")
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "hello another-long-secret-123", (), None)
    rec.correlation_id = "cid-1"
    line = JsonFormatter().format(rec)
    d = json.loads(line)
    assert d["correlation_id"] == "cid-1" and "another-long-secret-123" not in line


@pytest.mark.req("SEC-03")
def test_redaction_covers_dsn_passwords_quoted_json_and_compound_keys():
    r = Redactor()
    out = r.scrub(
        {
            "client_secret": "a",
            "x-auth-token": "b",
            "database_url": "postgresql://u:pw@h/db",
            "refresh_token": "c",
            "Set-Cookie": "d",
            "author": "Jane Doe",
            "domain": "example.com",
        }
    )
    assert all(
        out[k] == REDACTED
        for k in ("client_secret", "x-auth-token", "database_url", "refresh_token", "Set-Cookie")
    )
    assert out["author"] == "Jane Doe" and out["domain"] == "example.com"  # no over-redaction
    txt = r.scrub_text(
        'dsn postgresql+asyncpg://backlink:hunter2pw@localhost:55432/db and {"token": "abc123xyz"} '
        "and password='p4ssw0rd!' and api_key=zzz999"
    )
    for leaked in ("hunter2pw", "abc123xyz", "p4ssw0rd", "zzz999"):
        assert leaked not in txt
    assert "localhost:55432/db" in txt  # only the secret is removed, not the whole line


@pytest.mark.req("FR-27")
def test_real_send_cannot_be_enabled_in_shadow_mode(monkeypatch):
    from kb_contracts.enums import AutonomyMode

    with pytest.raises(ValidationError, match="REAL_SEND_ENABLED requires"):
        Settings(_env_file=None, real_send_enabled=True)
    monkeypatch.setenv("REAL_SEND_ENABLED", "true")  # the env-typo path
    monkeypatch.setenv("AUTONOMY_MODE", "shadow")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    assert Settings(
        _env_file=None, real_send_enabled=True, autonomy_mode=AutonomyMode.GUARDED
    ).real_send_enabled
