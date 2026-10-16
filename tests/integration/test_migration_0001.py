import re
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command

pytestmark = [pytest.mark.db, pytest.mark.req("NFR-12")]
ROOT = Path(__file__).resolve().parents[2]
APPEND_ONLY = [
    "audit_events",
    "outreach_events",
    "domain_metrics",
    "backlink_verifications",
    "report_snapshots",
]


def tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "select table_name from information_schema.tables "
            "where table_schema='public' and table_name <> 'alembic_version'"
        ).fetchall()
    return {r[0] for r in rows}


def documented_tables() -> set[str]:
    """Table names in backticks at the start of a docs/04 table row."""
    text = (ROOT / "docs/04_DATA_MODEL.md").read_text()
    section = text.split("## Tables", 1)[1].split("## Qdrant", 1)[0]
    names: set[str] = set()
    for line in section.splitlines():
        m = re.match(r"\| ((?:`[a-z_]+`(?: / )?)+) \|", line)
        if m:
            names.update(re.findall(r"`([a-z_]+)`", m.group(1)))
    return names


def test_up_down_up_roundtrip(alembic_cfg, scratch_db):
    _, dsn = scratch_db
    command.upgrade(alembic_cfg, "head")
    first = tables(dsn)
    assert len(first) == 48
    command.downgrade(alembic_cfg, "base")
    assert tables(dsn) == set()
    command.upgrade(alembic_cfg, "head")
    assert tables(dsn) == first


def test_schema_matches_docs04_exactly(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    assert tables(scratch_db[1]) == documented_tables()


def _seed_audit(c):
    c.execute(
        "insert into audit_events (actor_type, actor_id, action, subject_type, correlation_id) "
        "values ('system','test','x','y','cid')"
    )


@pytest.mark.req("SEC-06")
@pytest.mark.parametrize(
    "stmt",
    ["update audit_events set reason='x'", "delete from audit_events", "truncate audit_events"],
)
def test_audit_events_reject_update_delete_truncate(alembic_cfg, scratch_db, stmt):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        _seed_audit(c)
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="append-only"):
            c.execute(stmt)
        assert c.execute("select count(*) from audit_events").fetchone()[0] == 1


def test_all_append_only_tables_have_triggers(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1]) as c:
        got = {
            r[0]
            for r in c.execute(
                "select distinct event_object_table from information_schema.triggers "
                "where trigger_name like '%no_update_delete'"
            ).fetchall()
        }
    assert got == set(APPEND_ONLY)


def _campaign(c) -> uuid.UUID:
    return c.execute("insert into campaigns (name) values ('c') returning id").fetchone()[0]


@pytest.mark.req("FR-24")
def test_active_suppression_unique_but_supersession_allowed(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        ins = (
            "insert into suppression_entries (scope, value_normalized, reason) "
            "values ('email','a@b.com','opt_out')"
        )
        c.execute(ins)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(ins)  # global duplicate blocked
        c.execute("update suppression_entries set superseded_at = now()")
        c.execute(ins)  # re-adding after audited supersession is allowed


@pytest.mark.req("FR-09")
def test_send_idempotency_key_is_unique(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        camp = _campaign(c)
        dom = c.execute(
            "insert into domains (registered_domain) values ('a.com') returning id"
        ).fetchone()[0]
        pro = c.execute(
            "insert into prospects (campaign_id, domain_id) values (%s,%s) returning id",
            (camp, dom),
        ).fetchone()[0]
        con = c.execute(
            "insert into contacts (domain_id, confidence) values (%s, 0.9) returning id", (dom,)
        ).fetchone()[0]
        ch = c.execute(
            "insert into contact_channels (contact_id, channel_type, value_normalized, confidence)"
            " values (%s,'email','x@a.com',0.9) returning id",
            (con,),
        ).fetchone()[0]
        seq = c.execute(
            "insert into outreach_sequences (campaign_id, prospect_id, contact_channel_id) "
            "values (%s,%s,%s) returning id",
            (camp, pro, ch),
        ).fetchone()[0]
        msg = (
            "insert into outreach_messages (sequence_id, step_no, message_version, subject, body, "
            "idempotency_key) values (%s,1,1,'s','b','k1')"
        )
        c.execute(msg, (seq,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(msg, (seq,))
        # only one ACTIVE sequence per campaign+channel
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(
                "insert into outreach_sequences (campaign_id, prospect_id, contact_channel_id) "
                "values (%s,%s,%s)",
                (camp, pro, ch),
            )


def test_status_checks_reject_unknown_values_and_job_idempotency_unique(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("insert into campaigns (name, status) values ('c','BOGUS')")
        job = (
            "insert into scheduled_jobs (job_type, run_at, idempotency_key) "
            "values ('t', now(), 'j1')"
        )
        c.execute(job)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(job)
        # defaults are the safe ones
        row = c.execute(
            "insert into campaigns (name) values ('d') returning status, autonomy_mode"
        ).fetchone()
        assert row == ("DRAFT", "shadow")


def test_paid_provider_defaults_disabled(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        row = c.execute(
            "insert into provider_accounts (provider_type, provider_name) "
            "values ('email','mock') returning is_paid, enabled"
        ).fetchone()
        assert row == (False, False)


# --- hardening added after independent security + QA review ---------------------------------


def _live_message_setup(c):
    camp = _campaign(c)
    dom = c.execute(
        "insert into domains (registered_domain) values ('z.com') returning id"
    ).fetchone()[0]
    pro = c.execute(
        "insert into prospects (campaign_id, domain_id) values (%s,%s) returning id", (camp, dom)
    ).fetchone()[0]
    con = c.execute(
        "insert into contacts (domain_id, confidence) values (%s, 0.9) returning id", (dom,)
    ).fetchone()[0]
    ch = c.execute(
        "insert into contact_channels (contact_id, channel_type, value_normalized, confidence) "
        "values (%s,'email','q@z.com',0.9) returning id",
        (con,),
    ).fetchone()[0]
    return c.execute(
        "insert into outreach_sequences (campaign_id, prospect_id, contact_channel_id) "
        "values (%s,%s,%s) returning id",
        (camp, pro, ch),
    ).fetchone()[0]


@pytest.mark.req("FR-09")
def test_cannot_double_send_a_step_even_with_a_different_idempotency_key(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        seq = _live_message_setup(c)
        ins = (
            "insert into outreach_messages (sequence_id, step_no, message_version, subject, body, "
            "idempotency_key, status) values (%s,1,%s,'s','b',%s,%s)"
        )
        c.execute(ins, (seq, 1, "k-a", "SENT"))
        with pytest.raises(psycopg.errors.UniqueViolation):  # second live message, new key
            c.execute(ins, (seq, 2, "k-b", "QUEUED"))
        with pytest.raises(psycopg.errors.UniqueViolation):  # same step+version, new key
            c.execute(ins, (seq, 1, "k-c", "DRAFT"))
        # a non-live predecessor does not block a legitimate new version
        c.execute(ins, (seq, 3, "k-d", "DRAFT"))
        c.execute(
            "update outreach_messages set status='POLICY_BLOCKED' where idempotency_key='k-a'"
        )
        c.execute(ins, (seq, 4, "k-e", "QUEUED"))


@pytest.mark.req("FR-24")
def test_suppression_cannot_be_deleted_truncated_rewritten_or_case_bypassed(
    alembic_cfg, scratch_db
):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        c.execute(
            "insert into suppression_entries (scope, value_normalized, reason) "
            "values ('email','a@b.com','opt_out')"
        )
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="never deleted"):
            c.execute("delete from suppression_entries")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation):
            c.execute("truncate suppression_entries")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="permits updating"):
            c.execute("update suppression_entries set value_normalized='other@b.com'")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="permits updating"):
            c.execute("update suppression_entries set reason='manual'")
        with pytest.raises(psycopg.errors.CheckViolation):  # un-normalized value cannot hide
            c.execute(
                "insert into suppression_entries (scope, value_normalized, reason) "
                "values ('email','A@B.com','opt_out')"
            )
        c.execute("update suppression_entries set superseded_at=now(), superseded_by='op'")


@pytest.mark.req("FR-24")
def test_campaign_scoped_suppression_uniqueness(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        camp = _campaign(c)
        ins = (
            "insert into suppression_entries (scope, value_normalized, reason, campaign_id) "
            "values ('domain','x.com','manual',%s)"
        )
        c.execute(ins, (camp,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute(ins, (camp,))
        # same value globally is a distinct, allowed entry
        c.execute(
            "insert into suppression_entries (scope, value_normalized, reason) "
            "values ('domain','x.com','manual')"
        )


@pytest.mark.req("NFR-12")
def test_policy_versions_are_immutable_except_lifecycle_fields(alembic_cfg, scratch_db):
    command.upgrade(alembic_cfg, "head")
    with psycopg.connect(scratch_db[1], autocommit=True) as c:
        pol = c.execute(
            "insert into policies (key, scope) values ('p','global') returning id"
        ).fetchone()[0]
        c.execute(
            "insert into policy_versions (policy_id, version_no, body, content_hash, created_by) "
            "values (%s,1,'{\"min_da\":45}','h','op')",
            (pol,),
        )
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="permits updating"):
            c.execute("update policy_versions set body='{\"min_da\":0}'")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="permits updating"):
            c.execute("update policy_versions set content_hash='tampered'")
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="never deleted"):
            c.execute("delete from policy_versions")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("update policy_versions set status='BOGUS'")
        c.execute(
            "update policy_versions set status='ACTIVE', approved_by='mgr', approved_at=now()"
        )


def test_migration_enum_literals_match_contract_enums():
    """The migration inlines enum values on purpose; this fails if the contracts drift from it."""
    import importlib.util

    def load(path: Path, name: str):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    gen = load(ROOT / "infrastructure/scripts/gen_migration_0001.py", "gen0001")
    mig = load(ROOT / "migrations/versions/0001_baseline.py", "mig0001")
    for key, enum_cls in gen.NAMES.items():
        expected = ", ".join(f"'{m.value}'" for m in enum_cls)
        assert mig.ENUMS[key] == expected, f"{key}: migration enum differs from contracts"
