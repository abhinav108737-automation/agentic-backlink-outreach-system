# Canonical Data Model (migration 001 baseline)

Status: canonical (ADR-0011). Baseline is the PRD/TRD §15 model, normalized; no duplicate concepts under different names. Lifecycle states are defined in `docs/13_STATE_MACHINES.md`.

## Conventions
- PostgreSQL is authoritative. Qdrant is derived.
- Primary keys: UUIDv7 (`id uuid`). Timestamps: `timestamptz` in UTC; `created_at`, `updated_at` on every table.
- Mutable aggregates carry `version int` (optimistic locking). Lifecycle columns are named `status` and use the enum of their bounded machine.
- Tables are plural snake_case; FK columns are `<entity>_id`. Archive (status/`archived_at`) instead of hard delete.
- Rows created by a mutation carry `correlation_id`. Agent-produced rows carry `agent_run_id`. Policy-dependent rows carry `policy_version_id`.
- Append-only tables (`audit_events`, `outreach_events`, `domain_metrics`, `backlink_verifications`, `policy_versions`, `report_snapshots`) reject UPDATE/DELETE (DB trigger for `audit_events`; repository-level for others).
- PII minimization: store only contact data needed for outreach; normalize emails (lowercase, trimmed); never put secrets in DB rows (`secret_ref` points to the OS keyring).
- Every migration has a tested up/down/up path.

## Tables

### Campaign & policy
| Table | Key columns / constraints |
|---|---|
| `campaigns` | name, status (campaign machine), autonomy_mode (`shadow`/`guarded`/`autonomous`), policy_version_id, sender_provider_account_id, goals jsonb, version |
| `campaign_targets` | campaign_id, target_url, topic, keywords text[], anchor_preferences jsonb, priority |
| `policies` | key, scope (`global`/`campaign`), campaign_id NULL, risk_class (`L1`/`L2`/`L3`), name |
| `policy_versions` | policy_id, version_no (UNIQUE per policy), body jsonb (thresholds incl. min_da/max_spam, ranking weights, send limits, approval rules, missing-metric behavior), content_hash, status, created_by, approved_by, approved_at, source_proposal_id NULL. Immutable. |

### Domains & prospects
| Table | Key columns / constraints |
|---|---|
| `domains` | registered_domain (canonical, UNIQUE), first_seen_at. Global entity for cross-campaign dedupe, metrics and suppression. |
| `domain_metrics` | domain_id, provider, metric (`domain_authority`, `spam_score`, …), value numeric, fetched_at, source (`mock`/`manual_import`/…), raw_ref. Append-only time series. |
| `prospects` | campaign_id, domain_id, status (prospect machine), discovered_via, version. UNIQUE (campaign_id, domain_id). |
| `prospect_urls` | prospect_id, url, canonical_url, url_hash, page_type (`article`/`write_for_us`/`contact`/`about`/`editorial_guidelines`/`other`), provenance jsonb (provider, query, rank, discovered_at). UNIQUE (prospect_id, url_hash). |

### Qualification & ranking
| Table | Key columns / constraints |
|---|---|
| `qualification_decisions` | prospect_id, policy_version_id, outcome (`ELIGIBLE`/`REJECTED`/`DEFERRED`), reason_codes text[], metric_snapshot jsonb, missing_metrics text[], decided_at. Deterministic; advisory agent assessment referenced by agent_run_id NULL. |
| `ranking_results` | prospect_id, policy_version_id (weights), score, components jsonb, rank, explanation |

### Research & evidence
| Table | Key columns / constraints |
|---|---|
| `research_dossiers` | prospect_id, version_no, summary, topics text[], editorial_signals jsonb, status, agent_run_id |
| `evidence_items` | prospect_id, dossier_id NULL, source_url, excerpt, content_hash, kind (`topic`/`editorial_policy`/`contact`/`author`/`outbound_link`/…), extracted_at, extractor (`deterministic`/`agent`) |

### Contacts & suppression
| Table | Key columns / constraints |
|---|---|
| `contacts` | domain_id, name, role, contact_type (`editor`/`founder`/`author`/`generic`), confidence, provenance jsonb, status |
| `contact_channels` | contact_id, channel_type (`email`/`form`/`social`), value_normalized, verification_status, confidence, evidence_item_id |
| `suppression_entries` | scope (`email`/`domain`/`contact`), value_normalized, reason (`opt_out`/`bounce`/`complaint`/`manual`/`legal`), campaign_id NULL (= global), source_event_ref, created_by, superseded_at NULL, superseded_by NULL. Never deleted; lifting is an audited, governed supersession. Partial UNIQUE (scope, value_normalized, campaign_id) WHERE superseded_at IS NULL. |

### Outreach
| Table | Key columns / constraints |
|---|---|
| `outreach_sequences` | campaign_id, prospect_id, contact_channel_id, status (sequence machine), current_step, next_due_at, policy_version_id, version. Partial UNIQUE (campaign_id, contact_channel_id) WHERE status is active. |
| `outreach_threads` | sequence_id, provider_account_id, provider_thread_id, subject |
| `outreach_messages` | sequence_id, thread_id, step_no, message_version, status (message machine), subject, body, template_version, prompt_version, model_ref, agent_run_id, qa_result jsonb, claims jsonb (claim → evidence_item_ids), delivery_mode (`mock`/`real`), provider_message_id, **idempotency_key UNIQUE** (= campaign_id + contact_id + step_no + message_version) |
| `outreach_events` | message_id, sequence_id, type (`queued`/`sent`/`delivered`/`bounced`/`failed`/`cancelled`/…), provider_payload_ref, occurred_at. Append-only. |

### Replies
| Table | Key columns / constraints |
|---|---|
| `replies` | thread_id, sequence_id, provider_message_id UNIQUE, from_normalized, received_at, body_sanitized, raw_hash |
| `reply_classifications` | reply_id, class (`positive`/`question`/`negotiation`/`negative`/`opt_out`/`out_of_office`/`bounce`/`unknown`), confidence, next_action, deterministic_rule_applied NULL, return_date NULL, agent_run_id |

### Acquisition & verification
| Table | Key columns / constraints |
|---|---|
| `guest_post_opportunities` | prospect_id, sequence_id, status (opportunity machine), target_url, agreed_terms jsonb, requires_approval, version |
| `negotiations` | opportunity_id, round_no, draft, recommendation, risk_level, approval_request_id NULL, agent_run_id |
| `content_submissions` | opportunity_id, version_no, title, content_ref, submitted_at, status, published_url NULL |
| `backlinks` | opportunity_id NULL, source_url, target_url, expected_anchor, status (backlink machine), rel, first_seen_at, last_seen_at, last_checked_at, consecutive_misses, version |
| `backlink_verifications` | backlink_id, checked_at, http_status, final_url, canonical_url, link_found, anchor_text, rel_attrs text[], context_excerpt, result, verifier_version. Append-only. |

### Agentic runtime & scheduling
| Table | Key columns / constraints |
|---|---|
| `workflow_runs` | campaign_id, workflow_type, status (workflow machine), current_step, checkpoint jsonb, correlation_id, started_at, finished_at, error |
| `scheduled_jobs` | job_type, payload jsonb, run_at, status (job machine), priority, attempts, max_attempts, lease_owner, lease_expires_at, idempotency_key UNIQUE, concurrency_key, workflow_run_id NULL, subject_type/subject_id, last_error. Index (status, run_at). |
| `agent_runs` | campaign_id, workflow_run_id, agent, task_type, input_refs jsonb, model, provider, prompt_version, skill_version, structured_result jsonb, confidence, policy_version_id, latency_ms, tokens_in, tokens_out, failure, fallback_used, started_at, finished_at. Absorbs the former `model_runs`. |
| `tool_calls` | agent_run_id, tool_name, args_redacted jsonb, result_summary jsonb, status, policy_decision jsonb, latency_ms, correlation_id |
| `operator_decisions` | campaign_id, agent_run_id, action (`DELEGATE`/`SEND`/`WAIT`/`FOLLOW_UP`/`ESCALATE`/`STOP`/`VERIFY`/`REPORT`/`EXPERIMENT`), subject_id, rationale, evidence_ids uuid[], confidence, policy_version_id, tool_request jsonb, requires_human_approval |

### Governance
| Table | Key columns / constraints |
|---|---|
| `approval_requests` | kind (`live_send_activation`/`l3_policy_change`/`negotiation_commitment`/`message_send`/…), subject_type, subject_id, status (`PENDING`/`APPROVED`/`REJECTED`/`EXPIRED`), requested_by, decided_by, decided_at, reason |
| `operators` | display_name, local_profile, created_at. Local human actors for attribution. |
| `system_settings` | key UNIQUE (`kill_switch`, `real_send_enabled`, `autonomy_mode_default`, …), value jsonb, version. Changes audited; enabling real send requires an approved `live_send_activation`. |
| `idempotency_records` | key, scope, request_hash, response_ref, created_at. UNIQUE (scope, key). |
| `audit_events` | occurred_at, actor_type (`operator`/`agent`/`system`), actor_id, action, subject_type, subject_id, old_state jsonb, new_state jsonb, reason, correlation_id, causation_id, agent_run_id, policy_version_id. Append-only, DB trigger blocks UPDATE/DELETE. |

### Providers
| Table | Key columns / constraints |
|---|---|
| `provider_accounts` | provider_type, provider_name, display_name, config jsonb (non-secret), secret_ref (keyring key name), is_paid, enabled. Paid providers require explicit operator opt-in; never enabled silently. |
| `provider_health` | provider_account_id NULL, provider_name, checked_at, status, latency_ms, error |

### Memory
| Table | Key columns / constraints |
|---|---|
| `memories` | memory_type (`episodic`/`semantic`/`procedural`), subject_type, subject_id, content, source_refs jsonb, content_hash |
| `embedding_index_records` | source_table, source_id, collection, embedding_model, model_version, content_hash, indexed_at. UNIQUE (collection, source_table, source_id). Drives restartable Qdrant rebuilds. |

### Learning & optimization
| Table | Key columns / constraints |
|---|---|
| `feature_snapshots` | entity_type, entity_id, feature_set_version, features jsonb, as_of |
| `optimization_proposals` | target_kind (`ranking_weights`/`thresholds`/`prospect_priority`/`search_strategy`/`outreach_strategy`/`subject_lines`/`timing`/`contact_rules`/`prompt_skill`/`campaign_policy`), risk_class, status (optimization machine), baseline_ref, candidate jsonb, evidence jsonb, evaluation_results jsonb, approval_request_id NULL, created_by, version |
| `experiments` | proposal_id, design jsonb, status, arms jsonb, guardrails jsonb, started_at, ended_at, results jsonb |
| `experiment_assignments` | experiment_id, unit_type, unit_id, arm, assigned_at. UNIQUE (experiment_id, unit_type, unit_id). |

### Reporting
| Table | Key columns / constraints |
|---|---|
| `analytics_daily` / `analytics_weekly` | campaign_id NULL, period_start, metric_key, value, dims jsonb, source_watermark. Deterministic aggregates from events. |
| `weekly_reports` | campaign_id NULL, week_start, status, snapshot_id, narrative, narrative_agent_run_id |
| `report_snapshots` | payload jsonb, content_hash, generated_at, source_watermark. Immutable; a report regenerated from the same watermark yields the same hash. |

## Qdrant (derived)
Collections: research/evidence chunks, domain profiles, outreach/reply representations, memories. Every point stores the PostgreSQL source id, embedding model + version and content hash. Full rebuild from `evidence_items` and `memories` via `embedding_index_records`.

## Name normalization from earlier documents
| Earlier name (source) | Canonical |
|---|---|
| `prospect_metrics` (docs/04 v0) | `domain_metrics` |
| `prospect_research` (docs/04 v0) | `research_dossiers` + `evidence_items` |
| `contact_evidence` (docs/04 v0) | `evidence_items` (via `contact_channels.evidence_item_id`) |
| `threads` (PRD) / `outreach_threads` (docs/04 v0) | `outreach_threads` |
| `email_events` (PRD) | `outreach_events` |
| `reply_events` (docs/04 v0) | `replies` |
| `reports` (PRD) | `weekly_reports` |
| `memory_items` (docs/04 v0) | `memories` |
| `model_runs` (docs/04 v0) | merged into `agent_runs` (+ `tool_calls`) |
| `research_dossiers`, `domains`, `contact_channels`, `outreach_sequences`, `content_submissions`, `tool_calls`, `operator_decisions`, `suppression_entries`, `provider_accounts`, `analytics_daily/weekly` (PRD) | kept as-is |
| Added in reconciliation | `approval_requests`, `operators`, `system_settings`, `idempotency_records`, `feature_snapshots`, `embedding_index_records` (from docs/04 v0) |
