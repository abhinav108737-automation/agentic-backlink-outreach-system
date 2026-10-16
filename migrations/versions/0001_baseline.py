"""baseline schema (docs/04, ADR-0011): 48 tables, constraints, append-only triggers

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Enum values are inlined (not imported) so later contract changes cannot alter this migration.
ENUMS = {
    "campaign": "'DRAFT', 'ACTIVE', 'PAUSED', 'ARCHIVED'",
    "autonomy": "'shadow', 'guarded', 'autonomous'",
    "risk": "'L1', 'L2', 'L3'",
    "prospect": "'DISCOVERED', 'DEFERRED', 'ELIGIBLE', 'REJECTED', 'RANKED', 'RESEARCHED', 'CONTACT_READY', 'OUTREACH_READY', 'IN_OUTREACH', 'WON', 'LOST', 'SUPPRESSED'",
    "sequence": "'PLANNED', 'READY', 'POLICY_BLOCKED', 'WAITING_REPLY', 'FOLLOW_UP_DUE', 'COMPLETED_NO_REPLY', 'REPLIED', 'OPTED_OUT', 'BOUNCED', 'CANCELLED'",
    "message": "'DRAFT', 'QA_FAILED', 'QA_PASSED', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'POLICY_BLOCKED', 'QUEUED', 'SENDING', 'RETRY_WAIT', 'SENT', 'DELIVERED', 'BOUNCED', 'FAILED', 'CANCELLED'",
    "opportunity": "'OPEN', 'NEGOTIATING', 'AWAITING_APPROVAL', 'ESCALATED_TO_HUMAN', 'AGREED', 'CONTENT_IN_PROGRESS', 'CONTENT_SUBMITTED', 'REVISION_REQUESTED', 'PUBLISHED', 'LOST'",
    "backlink": "'EXPECTED', 'FOUND', 'VERIFIED', 'CHANGED', 'MISSING', 'LOST'",
    "optimization": "'PROPOSED', 'OFFLINE_TEST', 'SHADOW', 'CANARY', 'EXPERIMENT', 'PROMOTED', 'REJECTED', 'MONITORED', 'ROLLED_BACK'",
    "workflow": "'PENDING', 'RUNNING', 'WAITING', 'SUCCEEDED', 'FAILED', 'CANCELLED'",
    "job": "'SCHEDULED', 'RUNNING', 'RETRY_WAIT', 'SUCCEEDED', 'DEAD', 'CANCELLED'",
    "qual": "'ELIGIBLE', 'REJECTED', 'DEFERRED'",
    "reply": "'positive', 'question', 'negotiation', 'negative', 'opt_out', 'out_of_office', 'bounce', 'unknown'",
    "supp_scope": "'email', 'domain', 'contact'",
    "supp_reason": "'opt_out', 'bounce', 'complaint', 'manual', 'legal'",
    "approval_kind": "'live_send_activation', 'l3_policy_change', 'negotiation_commitment', 'message_send'",
    "approval_status": "'PENDING', 'APPROVED', 'REJECTED', 'EXPIRED'",
    "action": "'DELEGATE', 'SEND', 'WAIT', 'FOLLOW_UP', 'ESCALATE', 'STOP', 'VERIFY', 'REPORT', 'EXPERIMENT'",
    "delivery": "'mock', 'real'",
    "actor": "'operator', 'agent', 'system'",
    "memory": "'episodic', 'semantic', 'procedural'",
}

COMMON = (
    "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
    "created_at timestamptz NOT NULL DEFAULT now(), "
    "updated_at timestamptz NOT NULL DEFAULT now()"
)
VER = "version integer NOT NULL DEFAULT 1"
TABLES: list[str] = []
NIL = "'00000000-0000-0000-0000-000000000000'::uuid"


def chk(col: str, key: str, default: str | None = None) -> str:
    d = f" DEFAULT '{default}'" if default else ""
    return f"{col} text NOT NULL{d} CHECK ({col} IN ({ENUMS[key]}))"


def tbl(name: str, cols: str, *, ver: bool = False) -> None:
    parts = [COMMON] + ([VER] if ver else []) + [cols]
    op.execute(f"CREATE TABLE {name} ({', '.join(parts)})")
    TABLES.append(name)


def fk(ref: str, col: str, null: bool = False, cascade: bool = False) -> str:
    on = " ON DELETE CASCADE" if cascade else ""
    return f"{col} uuid {'' if null else 'NOT NULL '}REFERENCES {ref}(id){on}"


APPEND_ONLY = [
    "audit_events",
    "outreach_events",
    "domain_metrics",
    "backlink_verifications",
    "report_snapshots",
]
GUARDED = ["suppression_entries", "policy_versions"]
ALL_TABLES = """provider_accounts policies policy_versions campaigns campaign_targets workflow_runs
agent_runs approval_requests operators system_settings idempotency_records audit_events
provider_health domains domain_metrics prospects prospect_urls qualification_decisions
ranking_results research_dossiers evidence_items contacts contact_channels suppression_entries
outreach_sequences outreach_threads outreach_messages outreach_events replies reply_classifications
guest_post_opportunities negotiations content_submissions backlinks backlink_verifications
scheduled_jobs tool_calls operator_decisions memories embedding_index_records feature_snapshots
optimization_proposals experiments experiment_assignments analytics_daily analytics_weekly
weekly_reports report_snapshots""".split()


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")  # gen_random_uuid(); app uses UUIDv7

    # --- providers, policy, campaigns --------------------------------------------------------
    tbl(
        "provider_accounts",
        "provider_type text NOT NULL, provider_name text NOT NULL, "
        "display_name text, config jsonb NOT NULL DEFAULT '{}', secret_ref text, "
        "is_paid boolean NOT NULL DEFAULT false, enabled boolean NOT NULL DEFAULT false",
    )
    tbl(
        "policies",
        "key text NOT NULL UNIQUE, name text, "
        "scope text NOT NULL CHECK (scope IN ('global','campaign')), campaign_id uuid, "
        + chk("risk_class", "risk", "L3"),
    )
    tbl(
        "policy_versions",
        fk("policies", "policy_id") + ", version_no integer NOT NULL, "
        "body jsonb NOT NULL, content_hash text NOT NULL, "
        "status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','ACTIVE','SUPERSEDED')), "
        "created_by text NOT NULL, approved_by text, approved_at timestamptz, "
        "source_proposal_id uuid, UNIQUE (policy_id, version_no)",
    )
    tbl(
        "campaigns",
        "name text NOT NULL, "
        + chk("status", "campaign", "DRAFT")
        + ", "
        + chk("autonomy_mode", "autonomy", "shadow")
        + ", "
        + fk("policy_versions", "policy_version_id", null=True)
        + ", "
        + fk("provider_accounts", "sender_provider_account_id", null=True)
        + ", goals jsonb NOT NULL DEFAULT '{}', archived_at timestamptz",
        ver=True,
    )
    op.execute(
        "ALTER TABLE policies ADD CONSTRAINT policies_campaign_fk "
        "FOREIGN KEY (campaign_id) REFERENCES campaigns(id)"
    )
    tbl(
        "campaign_targets",
        fk("campaigns", "campaign_id", cascade=True)
        + ", target_url text NOT NULL, topic text, keywords text[] NOT NULL DEFAULT '{}', "
        "anchor_preferences jsonb NOT NULL DEFAULT '{}', priority integer NOT NULL DEFAULT 0",
    )

    # --- runtime / governance ----------------------------------------------------------------
    tbl(
        "workflow_runs",
        fk("campaigns", "campaign_id", null=True)
        + ", workflow_type text NOT NULL, "
        + chk("status", "workflow", "PENDING")
        + ", current_step text, checkpoint jsonb NOT NULL DEFAULT '{}', "
        "correlation_id text NOT NULL, started_at timestamptz, finished_at timestamptz, error text",
    )
    tbl(
        "agent_runs",
        fk("campaigns", "campaign_id", null=True)
        + ", "
        + fk("workflow_runs", "workflow_run_id", null=True)
        + ", agent text NOT NULL, "
        "task_type text NOT NULL, input_refs jsonb NOT NULL DEFAULT '{}', model text, "
        "provider text, prompt_version text, skill_version text, structured_result jsonb, "
        "confidence real, "
        + fk("policy_versions", "policy_version_id", null=True)
        + ", latency_ms integer, tokens_in integer, tokens_out integer, failure text, "
        "fallback_used boolean NOT NULL DEFAULT false, correlation_id text, "
        "started_at timestamptz, finished_at timestamptz",
    )
    tbl(
        "approval_requests",
        chk("kind", "approval_kind") + ", subject_type text NOT NULL, "
        "subject_id uuid NOT NULL, "
        + chk("status", "approval_status", "PENDING")
        + ", requested_by text NOT NULL, decided_by text, decided_at timestamptz, "
        "reason text, correlation_id text",
    )
    tbl("operators", "display_name text NOT NULL, local_profile text")
    tbl("system_settings", "key text NOT NULL UNIQUE, value jsonb NOT NULL", ver=True)
    tbl(
        "idempotency_records",
        "key text NOT NULL, scope text NOT NULL, "
        "request_hash text NOT NULL, response_ref text, UNIQUE (scope, key)",
    )
    tbl(
        "audit_events",
        "occurred_at timestamptz NOT NULL DEFAULT now(), "
        + chk("actor_type", "actor")
        + ", actor_id text NOT NULL, action text NOT NULL, "
        "subject_type text NOT NULL, subject_id uuid, old_state jsonb, new_state jsonb, "
        "reason text, correlation_id text NOT NULL, causation_id text, agent_run_id uuid, "
        "policy_version_id uuid",
    )
    tbl(
        "provider_health",
        fk("provider_accounts", "provider_account_id", null=True)
        + ", provider_name text NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(), "
        "status text NOT NULL, latency_ms integer, error text",
    )

    # --- domains / prospects -----------------------------------------------------------------
    tbl(
        "domains",
        "registered_domain text NOT NULL UNIQUE, first_seen_at timestamptz NOT NULL DEFAULT now()",
    )
    tbl(
        "domain_metrics",
        fk("domains", "domain_id") + ", provider text NOT NULL, "
        "metric text NOT NULL, value numeric, fetched_at timestamptz NOT NULL, "
        "source text NOT NULL, raw_ref text",
    )
    op.execute(
        "CREATE INDEX domain_metrics_lookup ON domain_metrics (domain_id, metric, fetched_at DESC)"
    )
    tbl(
        "prospects",
        fk("campaigns", "campaign_id")
        + ", "
        + fk("domains", "domain_id")
        + ", "
        + chk("status", "prospect", "DISCOVERED")
        + ", discovered_via text, "
        "UNIQUE (campaign_id, domain_id)",
        ver=True,
    )
    tbl(
        "prospect_urls",
        fk("prospects", "prospect_id", cascade=True) + ", url text NOT NULL, "
        "canonical_url text NOT NULL, url_hash text NOT NULL, "
        "page_type text NOT NULL DEFAULT 'other', provenance jsonb NOT NULL DEFAULT '{}', "
        "UNIQUE (prospect_id, url_hash)",
    )
    tbl(
        "qualification_decisions",
        fk("prospects", "prospect_id")
        + ", "
        + fk("policy_versions", "policy_version_id")
        + ", "
        + chk("outcome", "qual")
        + ", "
        "reason_codes text[] NOT NULL DEFAULT '{}', metric_snapshot jsonb NOT NULL DEFAULT '{}', "
        "missing_metrics text[] NOT NULL DEFAULT '{}', "
        "decided_at timestamptz NOT NULL DEFAULT now(), "
        + fk("agent_runs", "agent_run_id", null=True),
    )
    tbl(
        "ranking_results",
        fk("prospects", "prospect_id")
        + ", "
        + fk("policy_versions", "policy_version_id")
        + ", score double precision NOT NULL, "
        "components jsonb NOT NULL DEFAULT '{}', rank integer, "
        "explanation text NOT NULL DEFAULT ''",
    )
    tbl(
        "research_dossiers",
        fk("prospects", "prospect_id") + ", version_no integer NOT NULL, "
        "summary text NOT NULL, topics text[] NOT NULL DEFAULT '{}', "
        "editorial_signals jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'COMPLETE', "
        + fk("agent_runs", "agent_run_id", null=True)
        + ", UNIQUE (prospect_id, version_no)",
    )
    tbl(
        "evidence_items",
        fk("prospects", "prospect_id")
        + ", "
        + fk("research_dossiers", "dossier_id", null=True)
        + ", source_url text NOT NULL, "
        "excerpt text NOT NULL, content_hash text NOT NULL, kind text NOT NULL, "
        "extracted_at timestamptz NOT NULL, extractor text NOT NULL DEFAULT 'deterministic'",
    )

    # --- contacts & suppression --------------------------------------------------------------
    tbl(
        "contacts",
        fk("domains", "domain_id") + ", name text, role text, "
        "contact_type text NOT NULL DEFAULT 'generic', confidence real NOT NULL "
        "CHECK (confidence BETWEEN 0 AND 1), provenance jsonb NOT NULL DEFAULT '{}', "
        "status text NOT NULL DEFAULT 'ACTIVE'",
    )
    tbl(
        "contact_channels",
        fk("contacts", "contact_id", cascade=True)
        + ", channel_type text NOT NULL, value_normalized text NOT NULL, "
        "verification_status text NOT NULL DEFAULT 'unverified', "
        "confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1), "
        + fk("evidence_items", "evidence_item_id", null=True)
        + ", UNIQUE (contact_id, channel_type, value_normalized)",
    )
    tbl(
        "suppression_entries",
        chk("scope", "supp_scope") + ", value_normalized text NOT NULL "
        "CHECK (value_normalized = lower(btrim(value_normalized))), "
        + chk("reason", "supp_reason")
        + ", "
        + fk("campaigns", "campaign_id", null=True)
        + ", source_event_ref text, created_by text NOT NULL DEFAULT 'system', "
        "superseded_at timestamptz, superseded_by text",
    )
    op.execute(
        "CREATE UNIQUE INDEX suppression_active_uq ON suppression_entries "
        f"(scope, value_normalized, COALESCE(campaign_id, {NIL})) "
        "WHERE superseded_at IS NULL"
    )

    # --- outreach ----------------------------------------------------------------------------
    tbl(
        "outreach_sequences",
        fk("campaigns", "campaign_id")
        + ", "
        + fk("prospects", "prospect_id")
        + ", "
        + fk("contact_channels", "contact_channel_id")
        + ", "
        + chk("status", "sequence", "PLANNED")
        + ", current_step integer NOT NULL DEFAULT 0, "
        "next_due_at timestamptz, " + fk("policy_versions", "policy_version_id", null=True),
        ver=True,
    )
    op.execute(
        "CREATE UNIQUE INDEX sequence_one_active_per_channel ON outreach_sequences "
        "(campaign_id, contact_channel_id) WHERE status IN "
        "('PLANNED','READY','POLICY_BLOCKED','WAITING_REPLY','FOLLOW_UP_DUE')"
    )
    tbl(
        "outreach_threads",
        fk("outreach_sequences", "sequence_id")
        + ", "
        + fk("provider_accounts", "provider_account_id", null=True)
        + ", provider_thread_id text, subject text",
    )
    tbl(
        "outreach_messages",
        fk("outreach_sequences", "sequence_id")
        + ", "
        + fk("outreach_threads", "thread_id", null=True)
        + ", step_no integer NOT NULL, "
        "message_version integer NOT NULL, " + chk("status", "message", "DRAFT") + ", "
        "subject text NOT NULL, body text NOT NULL, template_version text, prompt_version text, "
        "model_ref text, "
        + fk("agent_runs", "agent_run_id", null=True)
        + ", qa_result jsonb NOT NULL DEFAULT '{}', claims jsonb NOT NULL DEFAULT '{}', "
        + chk("delivery_mode", "delivery", "mock")
        + ", provider_message_id text, "
        "attempts integer NOT NULL DEFAULT 0, idempotency_key text NOT NULL UNIQUE, "
        "UNIQUE (sequence_id, step_no, message_version)",
        ver=True,
    )
    # At most one live (queued/sending/sent) message per sequence step: a buggy or retried
    # sender cannot double-send even with a different idempotency key.
    op.execute(
        "CREATE UNIQUE INDEX one_live_message_per_step ON outreach_messages "
        "(sequence_id, step_no) WHERE status IN "
        "('QUEUED','SENDING','RETRY_WAIT','SENT','DELIVERED')"
    )
    tbl(
        "outreach_events",
        fk("outreach_messages", "message_id", null=True)
        + ", "
        + fk("outreach_sequences", "sequence_id", null=True)
        + ", type text NOT NULL, "
        "provider_payload_ref text, occurred_at timestamptz NOT NULL DEFAULT now()",
    )
    tbl(
        "replies",
        fk("outreach_threads", "thread_id", null=True)
        + ", "
        + fk("outreach_sequences", "sequence_id", null=True)
        + ", provider_message_id text NOT NULL UNIQUE, from_normalized text NOT NULL, "
        "received_at timestamptz NOT NULL, body_sanitized text NOT NULL, raw_hash text NOT NULL",
    )
    tbl(
        "reply_classifications",
        fk("replies", "reply_id") + ", " + chk("class", "reply") + ", "
        "confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1), "
        "next_action text NOT NULL, deterministic_rule_applied text, return_date timestamptz, "
        + fk("agent_runs", "agent_run_id", null=True),
    )

    # --- acquisition & verification ----------------------------------------------------------
    tbl(
        "guest_post_opportunities",
        fk("prospects", "prospect_id")
        + ", "
        + fk("outreach_sequences", "sequence_id", null=True)
        + ", "
        + chk("status", "opportunity", "OPEN")
        + ", target_url text, "
        "agreed_terms jsonb NOT NULL DEFAULT '{}', "
        "requires_approval boolean NOT NULL DEFAULT false",
        ver=True,
    )
    tbl(
        "negotiations",
        fk("guest_post_opportunities", "opportunity_id")
        + ", round_no integer NOT NULL, draft text, recommendation text, "
        "risk_level text CHECK (risk_level IN ('low','medium','high')), "
        + fk("approval_requests", "approval_request_id", null=True)
        + ", "
        + fk("agent_runs", "agent_run_id", null=True),
    )
    tbl(
        "content_submissions",
        fk("guest_post_opportunities", "opportunity_id")
        + ", version_no integer NOT NULL, title text, content_ref text, "
        "submitted_at timestamptz, status text NOT NULL DEFAULT 'DRAFT', published_url text",
    )
    tbl(
        "backlinks",
        fk("guest_post_opportunities", "opportunity_id", null=True) + ", "
        "source_url text NOT NULL, target_url text NOT NULL, expected_anchor text, "
        + chk("status", "backlink", "EXPECTED")
        + ", rel text[] NOT NULL DEFAULT '{}', "
        "first_seen_at timestamptz, last_seen_at timestamptz, last_checked_at timestamptz, "
        "consecutive_misses integer NOT NULL DEFAULT 0",
        ver=True,
    )
    tbl(
        "backlink_verifications",
        fk("backlinks", "backlink_id")
        + ", checked_at timestamptz NOT NULL, http_status integer, final_url text, "
        "canonical_url text, link_found boolean NOT NULL, anchor_text text, "
        "rel_attrs text[] NOT NULL DEFAULT '{}', context_excerpt text, "
        "result text NOT NULL, verifier_version text",
    )

    # --- scheduling / agent trail ------------------------------------------------------------
    tbl(
        "scheduled_jobs",
        "job_type text NOT NULL, payload jsonb NOT NULL DEFAULT '{}', "
        "run_at timestamptz NOT NULL, " + chk("status", "job", "SCHEDULED") + ", "
        "priority integer NOT NULL DEFAULT 0, attempts integer NOT NULL DEFAULT 0, "
        "max_attempts integer NOT NULL DEFAULT 5, lease_owner text, lease_expires_at timestamptz, "
        "idempotency_key text NOT NULL UNIQUE, concurrency_key text, "
        + fk("workflow_runs", "workflow_run_id", null=True)
        + ", subject_type text, subject_id uuid, last_error text",
    )
    op.execute("CREATE INDEX scheduled_jobs_due ON scheduled_jobs (status, run_at)")
    tbl(
        "tool_calls",
        fk("agent_runs", "agent_run_id") + ", tool_name text NOT NULL, "
        "args_redacted jsonb NOT NULL DEFAULT '{}', result_summary jsonb, "
        "status text NOT NULL, policy_decision jsonb, latency_ms integer, correlation_id text",
    )
    tbl(
        "operator_decisions",
        fk("campaigns", "campaign_id")
        + ", "
        + fk("agent_runs", "agent_run_id", null=True)
        + ", "
        + chk("action", "action")
        + ", "
        "subject_id uuid, rationale text NOT NULL, evidence_ids uuid[] NOT NULL DEFAULT '{}', "
        "confidence real NOT NULL CHECK (confidence BETWEEN 0 AND 1), "
        + fk("policy_versions", "policy_version_id", null=True)
        + ", tool_request jsonb, requires_human_approval boolean NOT NULL DEFAULT false",
    )

    # --- memory / learning / reporting -------------------------------------------------------
    tbl(
        "memories",
        chk("memory_type", "memory") + ", subject_type text NOT NULL, "
        "subject_id uuid, content text NOT NULL, source_refs jsonb NOT NULL DEFAULT '{}', "
        "content_hash text NOT NULL",
    )
    tbl(
        "embedding_index_records",
        "source_table text NOT NULL, source_id uuid NOT NULL, "
        "collection text NOT NULL, embedding_model text NOT NULL, model_version text NOT NULL, "
        "content_hash text NOT NULL, indexed_at timestamptz NOT NULL DEFAULT now(), "
        "UNIQUE (collection, source_table, source_id)",
    )
    tbl(
        "feature_snapshots",
        "entity_type text NOT NULL, entity_id uuid NOT NULL, "
        "feature_set_version text NOT NULL, features jsonb NOT NULL, as_of timestamptz NOT NULL",
    )
    tbl(
        "optimization_proposals",
        "target_kind text NOT NULL, "
        + chk("risk_class", "risk")
        + ", "
        + chk("status", "optimization", "PROPOSED")
        + ", baseline_ref text, "
        "candidate jsonb NOT NULL, evidence jsonb NOT NULL DEFAULT '{}', "
        "evaluation_results jsonb NOT NULL DEFAULT '{}', "
        + fk("approval_requests", "approval_request_id", null=True)
        + ", created_by text NOT NULL DEFAULT 'system'",
        ver=True,
    )
    op.execute(
        "ALTER TABLE policy_versions ADD CONSTRAINT policy_versions_proposal_fk "
        "FOREIGN KEY (source_proposal_id) REFERENCES optimization_proposals(id)"
    )
    tbl(
        "experiments",
        fk("optimization_proposals", "proposal_id")
        + ", design jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'PLANNED', "
        "arms jsonb NOT NULL DEFAULT '[]', guardrails jsonb NOT NULL DEFAULT '{}', "
        "started_at timestamptz, ended_at timestamptz, results jsonb",
    )
    tbl(
        "experiment_assignments",
        fk("experiments", "experiment_id")
        + ", unit_type text NOT NULL, unit_id uuid NOT NULL, arm text NOT NULL, "
        "assigned_at timestamptz NOT NULL DEFAULT now(), UNIQUE (experiment_id, unit_type, unit_id)",
    )
    for name in ("analytics_daily", "analytics_weekly"):
        tbl(
            name,
            fk("campaigns", "campaign_id", null=True) + ", period_start date NOT NULL, "
            "metric_key text NOT NULL, value double precision NOT NULL, "
            "dims jsonb NOT NULL DEFAULT '{}', source_watermark text NOT NULL",
        )
    tbl(
        "report_snapshots",
        "payload jsonb NOT NULL, content_hash text NOT NULL, "
        "generated_at timestamptz NOT NULL DEFAULT now(), source_watermark text NOT NULL",
    )
    tbl(
        "weekly_reports",
        fk("campaigns", "campaign_id", null=True)
        + ", week_start date NOT NULL, status text NOT NULL DEFAULT 'DRAFT', "
        + fk("report_snapshots", "snapshot_id", null=True)
        + ", narrative text, "
        + fk("agent_runs", "narrative_agent_run_id", null=True),
    )

    # --- append-only enforcement -------------------------------------------------------------
    op.execute("""CREATE FUNCTION kb_forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION '% is append-only (% not allowed)', TG_TABLE_NAME, TG_OP
            USING ERRCODE = 'integrity_constraint_violation'; END $$""")
    for t in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {t}_no_update_delete BEFORE UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION kb_forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION kb_forbid_mutation()"
        )

    op.execute("""CREATE FUNCTION kb_restrict_update() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE c text; o jsonb; n jsonb;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          o := to_jsonb(OLD) - 'updated_at'; n := to_jsonb(NEW) - 'updated_at';
          FOREACH c IN ARRAY TG_ARGV LOOP o := o - c; n := n - c; END LOOP;
          IF o IS DISTINCT FROM n THEN
            RAISE EXCEPTION '% permits updating only: %', TG_TABLE_NAME, array_to_string(TG_ARGV, ',')
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END $$""")
    # Suppression is never deleted and only supersession fields may change (governed + audited).
    op.execute(
        "CREATE TRIGGER suppression_entries_guard BEFORE UPDATE OR DELETE ON suppression_entries "
        "FOR EACH ROW EXECUTE FUNCTION kb_restrict_update('superseded_at','superseded_by')"
    )
    # Policy versions are immutable: only lifecycle/approval fields may change.
    op.execute(
        "CREATE TRIGGER policy_versions_guard BEFORE UPDATE OR DELETE ON policy_versions "
        "FOR EACH ROW EXECUTE FUNCTION kb_restrict_update('status','approved_by','approved_at')"
    )
    for t in GUARDED:
        op.execute(
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION kb_forbid_mutation()"
        )

    drift = set(ALL_TABLES) ^ set(TABLES)
    assert not drift, f"table list drift: {sorted(drift)}"


def downgrade() -> None:
    op.execute(
        "ALTER TABLE IF EXISTS policy_versions "
        "DROP CONSTRAINT IF EXISTS policy_versions_proposal_fk"
    )
    op.execute("ALTER TABLE IF EXISTS policies DROP CONSTRAINT IF EXISTS policies_campaign_fk")
    for t in reversed(ALL_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS kb_forbid_mutation()")
    op.execute("DROP FUNCTION IF EXISTS kb_restrict_update()")
