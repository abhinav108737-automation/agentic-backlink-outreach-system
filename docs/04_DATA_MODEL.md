# Data Model

Core entities: campaigns, campaign_targets, prospects, prospect_urls, prospect_metrics, prospect_research, contacts, contact_evidence, qualification_decisions, ranking_results, outreach_threads, outreach_messages, outreach_events, reply_events, reply_classifications, negotiations, guest_post_opportunities, backlinks, backlink_verifications, agent_runs, workflow_runs, scheduled_jobs, policies, policy_versions, optimization_proposals, experiments, experiment_assignments, weekly_reports, report_snapshots, audit_events, provider_health, model_runs, memory_items, embedding_index_records.

Critical rules:
- canonical domain uniqueness policy
- idempotency keys for sends/mutations
- versioned policy
- immutable audit history
- UTC timestamps
- PII minimization
- Qdrant points reference PostgreSQL IDs
- embedding model/version/content hash
- restartable reindex/rebuild

Enums:
ProspectStatus: DISCOVERED, REJECTED, ELIGIBLE, RANKED, RESEARCHED, CONTACT_READY, OUTREACH_READY, CONTACTED, REPLIED, NEGOTIATING, WON, LOST, SUPPRESSED.
OutreachStatus: DRAFT, POLICY_BLOCKED, READY, SENT, DELIVERED, BOUNCED, REPLIED, FOLLOWUP_SCHEDULED, CLOSED, OPTED_OUT.
BacklinkStatus: EXPECTED, FOUND, VERIFIED, CHANGED, MISSING, LOST.
OptimizationStatus: PROPOSED, OFFLINE_TEST, SHADOW, CANARY, EXPERIMENT, PROMOTED, REJECTED, ROLLED_BACK.
