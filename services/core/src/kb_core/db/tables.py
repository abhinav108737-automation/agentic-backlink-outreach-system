"""Typed SQLAlchemy Core metadata over the schema owned by `migrations/versions/0001_baseline.py`.

The migration owns the schema; this module is only a typed accessor for it. Deliberate
consequences of that split:

- Core `Table` objects, not declarative ORM models: there is no mapper, no `create_all` and no
  Alembic autogenerate target, so no code path can create or alter a table from here and drift
  away from the migration.
- CHECK constraints, UNIQUE indexes, foreign keys and the append-only/guard triggers are
  intentionally NOT re-declared here. They are enforced by PostgreSQL. Re-stating them would
  invite two divergent truths; `tests/integration/db/test_table_metadata.py` reflects the live
  database and asserts table and column names match this metadata, so drift fails a test
  instead of being assumed absent.
- No `updated_at` trigger exists in the schema, so every UPDATE issued by a repository sets
  `updated_at` explicitly.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

METADATA = sa.MetaData()

agent_runs = sa.Table(
    "agent_runs",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("workflow_run_id", pg.UUID(as_uuid=True)),
    sa.Column("agent", sa.Text(), nullable=False),
    sa.Column("task_type", sa.Text(), nullable=False),
    sa.Column("input_refs", pg.JSONB(), nullable=False),
    sa.Column("model", sa.Text()),
    sa.Column("provider", sa.Text()),
    sa.Column("prompt_version", sa.Text()),
    sa.Column("skill_version", sa.Text()),
    sa.Column("structured_result", pg.JSONB()),
    sa.Column("confidence", sa.REAL()),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True)),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("tokens_in", sa.Integer()),
    sa.Column("tokens_out", sa.Integer()),
    sa.Column("failure", sa.Text()),
    sa.Column("fallback_used", sa.Boolean(), nullable=False),
    sa.Column("correlation_id", sa.Text()),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
)

analytics_daily = sa.Table(
    "analytics_daily",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("period_start", sa.Date(), nullable=False),
    sa.Column("metric_key", sa.Text(), nullable=False),
    sa.Column("value", sa.Double(), nullable=False),
    sa.Column("dims", pg.JSONB(), nullable=False),
    sa.Column("source_watermark", sa.Text(), nullable=False),
)

analytics_weekly = sa.Table(
    "analytics_weekly",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("period_start", sa.Date(), nullable=False),
    sa.Column("metric_key", sa.Text(), nullable=False),
    sa.Column("value", sa.Double(), nullable=False),
    sa.Column("dims", pg.JSONB(), nullable=False),
    sa.Column("source_watermark", sa.Text(), nullable=False),
)

approval_requests = sa.Table(
    "approval_requests",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("subject_type", sa.Text(), nullable=False),
    sa.Column("subject_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("requested_by", sa.Text(), nullable=False),
    sa.Column("decided_by", sa.Text()),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("reason", sa.Text()),
    sa.Column("correlation_id", sa.Text()),
)

audit_events = sa.Table(
    "audit_events",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("actor_type", sa.Text(), nullable=False),
    sa.Column("actor_id", sa.Text(), nullable=False),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("subject_type", sa.Text(), nullable=False),
    sa.Column("subject_id", pg.UUID(as_uuid=True)),
    sa.Column("old_state", pg.JSONB()),
    sa.Column("new_state", pg.JSONB()),
    sa.Column("reason", sa.Text()),
    sa.Column("correlation_id", sa.Text(), nullable=False),
    sa.Column("causation_id", sa.Text()),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True)),
)

backlink_verifications = sa.Table(
    "backlink_verifications",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("backlink_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("http_status", sa.Integer()),
    sa.Column("final_url", sa.Text()),
    sa.Column("canonical_url", sa.Text()),
    sa.Column("link_found", sa.Boolean(), nullable=False),
    sa.Column("anchor_text", sa.Text()),
    sa.Column("rel_attrs", pg.ARRAY(sa.Text()), nullable=False),
    sa.Column("context_excerpt", sa.Text()),
    sa.Column("result", sa.Text(), nullable=False),
    sa.Column("verifier_version", sa.Text()),
)

backlinks = sa.Table(
    "backlinks",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("opportunity_id", pg.UUID(as_uuid=True)),
    sa.Column("source_url", sa.Text(), nullable=False),
    sa.Column("target_url", sa.Text(), nullable=False),
    sa.Column("expected_anchor", sa.Text()),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("rel", pg.ARRAY(sa.Text()), nullable=False),
    sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("last_checked_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("consecutive_misses", sa.Integer(), nullable=False),
)

campaign_targets = sa.Table(
    "campaign_targets",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("target_url", sa.Text(), nullable=False),
    sa.Column("topic", sa.Text()),
    sa.Column("keywords", pg.ARRAY(sa.Text()), nullable=False),
    sa.Column("anchor_preferences", pg.JSONB(), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False),
)

campaigns = sa.Table(
    "campaigns",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("name", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("autonomy_mode", sa.Text(), nullable=False),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True)),
    sa.Column("sender_provider_account_id", pg.UUID(as_uuid=True)),
    sa.Column("goals", pg.JSONB(), nullable=False),
    sa.Column("archived_at", sa.TIMESTAMP(timezone=True)),
)

contact_channels = sa.Table(
    "contact_channels",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("contact_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("channel_type", sa.Text(), nullable=False),
    sa.Column("value_normalized", sa.Text(), nullable=False),
    sa.Column("verification_status", sa.Text(), nullable=False),
    sa.Column("confidence", sa.REAL(), nullable=False),
    sa.Column("evidence_item_id", pg.UUID(as_uuid=True)),
)

contacts = sa.Table(
    "contacts",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("domain_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("name", sa.Text()),
    sa.Column("role", sa.Text()),
    sa.Column("contact_type", sa.Text(), nullable=False),
    sa.Column("confidence", sa.REAL(), nullable=False),
    sa.Column("provenance", pg.JSONB(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
)

content_submissions = sa.Table(
    "content_submissions",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("opportunity_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("title", sa.Text()),
    sa.Column("content_ref", sa.Text()),
    sa.Column("submitted_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("published_url", sa.Text()),
)

domain_metrics = sa.Table(
    "domain_metrics",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("domain_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("provider", sa.Text(), nullable=False),
    sa.Column("metric", sa.Text(), nullable=False),
    sa.Column("value", sa.Numeric()),
    sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("source", sa.Text(), nullable=False),
    sa.Column("raw_ref", sa.Text()),
)

domains = sa.Table(
    "domains",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("registered_domain", sa.Text(), nullable=False),
    sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

embedding_index_records = sa.Table(
    "embedding_index_records",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("source_table", sa.Text(), nullable=False),
    sa.Column("source_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("collection", sa.Text(), nullable=False),
    sa.Column("embedding_model", sa.Text(), nullable=False),
    sa.Column("model_version", sa.Text(), nullable=False),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("indexed_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

evidence_items = sa.Table(
    "evidence_items",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("dossier_id", pg.UUID(as_uuid=True)),
    sa.Column("source_url", sa.Text(), nullable=False),
    sa.Column("excerpt", sa.Text(), nullable=False),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("kind", sa.Text(), nullable=False),
    sa.Column("extracted_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("extractor", sa.Text(), nullable=False),
)

experiment_assignments = sa.Table(
    "experiment_assignments",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("experiment_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("unit_type", sa.Text(), nullable=False),
    sa.Column("unit_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("arm", sa.Text(), nullable=False),
    sa.Column("assigned_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

experiments = sa.Table(
    "experiments",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("proposal_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("design", pg.JSONB(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("arms", pg.JSONB(), nullable=False),
    sa.Column("guardrails", pg.JSONB(), nullable=False),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("ended_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("results", pg.JSONB()),
)

feature_snapshots = sa.Table(
    "feature_snapshots",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("entity_type", sa.Text(), nullable=False),
    sa.Column("entity_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("feature_set_version", sa.Text(), nullable=False),
    sa.Column("features", pg.JSONB(), nullable=False),
    sa.Column("as_of", sa.TIMESTAMP(timezone=True), nullable=False),
)

guest_post_opportunities = sa.Table(
    "guest_post_opportunities",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("sequence_id", pg.UUID(as_uuid=True)),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("target_url", sa.Text()),
    sa.Column("agreed_terms", pg.JSONB(), nullable=False),
    sa.Column("requires_approval", sa.Boolean(), nullable=False),
)

idempotency_records = sa.Table(
    "idempotency_records",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("key", sa.Text(), nullable=False),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("request_hash", sa.Text(), nullable=False),
    sa.Column("response_ref", sa.Text()),
)

memories = sa.Table(
    "memories",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("memory_type", sa.Text(), nullable=False),
    sa.Column("subject_type", sa.Text(), nullable=False),
    sa.Column("subject_id", pg.UUID(as_uuid=True)),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("source_refs", pg.JSONB(), nullable=False),
    sa.Column("content_hash", sa.Text(), nullable=False),
)

negotiations = sa.Table(
    "negotiations",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("opportunity_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("round_no", sa.Integer(), nullable=False),
    sa.Column("draft", sa.Text()),
    sa.Column("recommendation", sa.Text()),
    sa.Column("risk_level", sa.Text()),
    sa.Column("approval_request_id", pg.UUID(as_uuid=True)),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
)

operator_decisions = sa.Table(
    "operator_decisions",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
    sa.Column("action", sa.Text(), nullable=False),
    sa.Column("subject_id", pg.UUID(as_uuid=True)),
    sa.Column("rationale", sa.Text(), nullable=False),
    sa.Column("evidence_ids", pg.ARRAY(pg.UUID(as_uuid=True)), nullable=False),
    sa.Column("confidence", sa.REAL(), nullable=False),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True)),
    sa.Column("tool_request", pg.JSONB()),
    sa.Column("requires_human_approval", sa.Boolean(), nullable=False),
)

operators = sa.Table(
    "operators",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("local_profile", sa.Text()),
)

optimization_proposals = sa.Table(
    "optimization_proposals",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("target_kind", sa.Text(), nullable=False),
    sa.Column("risk_class", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("baseline_ref", sa.Text()),
    sa.Column("candidate", pg.JSONB(), nullable=False),
    sa.Column("evidence", pg.JSONB(), nullable=False),
    sa.Column("evaluation_results", pg.JSONB(), nullable=False),
    sa.Column("approval_request_id", pg.UUID(as_uuid=True)),
    sa.Column("created_by", sa.Text(), nullable=False),
)

outreach_events = sa.Table(
    "outreach_events",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("message_id", pg.UUID(as_uuid=True)),
    sa.Column("sequence_id", pg.UUID(as_uuid=True)),
    sa.Column("type", sa.Text(), nullable=False),
    sa.Column("provider_payload_ref", sa.Text()),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

outreach_messages = sa.Table(
    "outreach_messages",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("sequence_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("thread_id", pg.UUID(as_uuid=True)),
    sa.Column("step_no", sa.Integer(), nullable=False),
    sa.Column("message_version", sa.Integer(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("subject", sa.Text(), nullable=False),
    sa.Column("body", sa.Text(), nullable=False),
    sa.Column("template_version", sa.Text()),
    sa.Column("prompt_version", sa.Text()),
    sa.Column("model_ref", sa.Text()),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
    sa.Column("qa_result", pg.JSONB(), nullable=False),
    sa.Column("claims", pg.JSONB(), nullable=False),
    sa.Column("delivery_mode", sa.Text(), nullable=False),
    sa.Column("provider_message_id", sa.Text()),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
)

outreach_sequences = sa.Table(
    "outreach_sequences",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("contact_channel_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("current_step", sa.Integer(), nullable=False),
    sa.Column("next_due_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True)),
)

outreach_threads = sa.Table(
    "outreach_threads",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("sequence_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("provider_account_id", pg.UUID(as_uuid=True)),
    sa.Column("provider_thread_id", sa.Text()),
    sa.Column("subject", sa.Text()),
)

policies = sa.Table(
    "policies",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("key", sa.Text(), nullable=False),
    sa.Column("name", sa.Text()),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("risk_class", sa.Text(), nullable=False),
)

policy_versions = sa.Table(
    "policy_versions",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("policy_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("body", pg.JSONB(), nullable=False),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("approved_by", sa.Text()),
    sa.Column("approved_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("source_proposal_id", pg.UUID(as_uuid=True)),
)

prospect_urls = sa.Table(
    "prospect_urls",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("url", sa.Text(), nullable=False),
    sa.Column("canonical_url", sa.Text(), nullable=False),
    sa.Column("url_hash", sa.Text(), nullable=False),
    sa.Column("page_type", sa.Text(), nullable=False),
    sa.Column("provenance", pg.JSONB(), nullable=False),
)

prospects = sa.Table(
    "prospects",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("domain_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("discovered_via", sa.Text()),
)

provider_accounts = sa.Table(
    "provider_accounts",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("provider_type", sa.Text(), nullable=False),
    sa.Column("provider_name", sa.Text(), nullable=False),
    sa.Column("display_name", sa.Text()),
    sa.Column("config", pg.JSONB(), nullable=False),
    sa.Column("secret_ref", sa.Text()),
    sa.Column("is_paid", sa.Boolean(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
)

provider_health = sa.Table(
    "provider_health",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("provider_account_id", pg.UUID(as_uuid=True)),
    sa.Column("provider_name", sa.Text(), nullable=False),
    sa.Column("checked_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("error", sa.Text()),
)

qualification_decisions = sa.Table(
    "qualification_decisions",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("outcome", sa.Text(), nullable=False),
    sa.Column("reason_codes", pg.ARRAY(sa.Text()), nullable=False),
    sa.Column("metric_snapshot", pg.JSONB(), nullable=False),
    sa.Column("missing_metrics", pg.ARRAY(sa.Text()), nullable=False),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
)

ranking_results = sa.Table(
    "ranking_results",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("policy_version_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("score", sa.Double(), nullable=False),
    sa.Column("components", pg.JSONB(), nullable=False),
    sa.Column("rank", sa.Integer()),
    sa.Column("explanation", sa.Text(), nullable=False),
)

replies = sa.Table(
    "replies",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("thread_id", pg.UUID(as_uuid=True)),
    sa.Column("sequence_id", pg.UUID(as_uuid=True)),
    sa.Column("provider_message_id", sa.Text(), nullable=False),
    sa.Column("from_normalized", sa.Text(), nullable=False),
    sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("body_sanitized", sa.Text(), nullable=False),
    sa.Column("raw_hash", sa.Text(), nullable=False),
)

reply_classifications = sa.Table(
    "reply_classifications",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("reply_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("class", sa.Text(), nullable=False),
    sa.Column("confidence", sa.REAL(), nullable=False),
    sa.Column("next_action", sa.Text(), nullable=False),
    sa.Column("deterministic_rule_applied", sa.Text()),
    sa.Column("return_date", sa.TIMESTAMP(timezone=True)),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
)

report_snapshots = sa.Table(
    "report_snapshots",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("payload", pg.JSONB(), nullable=False),
    sa.Column("content_hash", sa.Text(), nullable=False),
    sa.Column("generated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("source_watermark", sa.Text(), nullable=False),
)

research_dossiers = sa.Table(
    "research_dossiers",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("prospect_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("version_no", sa.Integer(), nullable=False),
    sa.Column("summary", sa.Text(), nullable=False),
    sa.Column("topics", pg.ARRAY(sa.Text()), nullable=False),
    sa.Column("editorial_signals", pg.JSONB(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True)),
)

scheduled_jobs = sa.Table(
    "scheduled_jobs",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("job_type", sa.Text(), nullable=False),
    sa.Column("payload", pg.JSONB(), nullable=False),
    sa.Column("run_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("priority", sa.Integer(), nullable=False),
    sa.Column("attempts", sa.Integer(), nullable=False),
    sa.Column("max_attempts", sa.Integer(), nullable=False),
    sa.Column("lease_owner", sa.Text()),
    sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("idempotency_key", sa.Text(), nullable=False),
    sa.Column("concurrency_key", sa.Text()),
    sa.Column("workflow_run_id", pg.UUID(as_uuid=True)),
    sa.Column("subject_type", sa.Text()),
    sa.Column("subject_id", pg.UUID(as_uuid=True)),
    sa.Column("last_error", sa.Text()),
)

suppression_entries = sa.Table(
    "suppression_entries",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("scope", sa.Text(), nullable=False),
    sa.Column("value_normalized", sa.Text(), nullable=False),
    sa.Column("reason", sa.Text(), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("source_event_ref", sa.Text()),
    sa.Column("created_by", sa.Text(), nullable=False),
    sa.Column("superseded_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("superseded_by", sa.Text()),
)

system_settings = sa.Table(
    "system_settings",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("key", sa.Text(), nullable=False),
    sa.Column("value", pg.JSONB(), nullable=False),
)

tool_calls = sa.Table(
    "tool_calls",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("agent_run_id", pg.UUID(as_uuid=True), nullable=False),
    sa.Column("tool_name", sa.Text(), nullable=False),
    sa.Column("args_redacted", pg.JSONB(), nullable=False),
    sa.Column("result_summary", pg.JSONB()),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("policy_decision", pg.JSONB()),
    sa.Column("latency_ms", sa.Integer()),
    sa.Column("correlation_id", sa.Text()),
)

weekly_reports = sa.Table(
    "weekly_reports",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("week_start", sa.Date(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("snapshot_id", pg.UUID(as_uuid=True)),
    sa.Column("narrative", sa.Text()),
    sa.Column("narrative_agent_run_id", pg.UUID(as_uuid=True)),
)

workflow_runs = sa.Table(
    "workflow_runs",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("campaign_id", pg.UUID(as_uuid=True)),
    sa.Column("workflow_type", sa.Text(), nullable=False),
    sa.Column("status", sa.Text(), nullable=False),
    sa.Column("current_step", sa.Text()),
    sa.Column("checkpoint", pg.JSONB(), nullable=False),
    sa.Column("correlation_id", sa.Text(), nullable=False),
    sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
    sa.Column("error", sa.Text()),
)

#: Tables PostgreSQL protects with `kb_forbid_mutation()` (no UPDATE/DELETE/TRUNCATE).
APPEND_ONLY_TABLES: frozenset[str] = frozenset(
    {
        "audit_events",
        "outreach_events",
        "domain_metrics",
        "backlink_verifications",
        "report_snapshots",
    }
)

#: Tables PostgreSQL protects with `kb_restrict_update()`, mapped to the only updatable columns.
GUARDED_UPDATABLE_COLUMNS: dict[str, frozenset[str]] = {
    "suppression_entries": frozenset({"superseded_at", "superseded_by", "updated_at"}),
    "policy_versions": frozenset({"status", "approved_by", "approved_at", "updated_at"}),
}
