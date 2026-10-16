# ADR-0003: PostgreSQL is authoritative, Qdrant is derived

Status: Accepted. Date: 2026-10-16

## Context
The system mixes relational, auditable business state with semantic retrieval. Vector stores are lossy, re-embeddable and tied to a model version, so they cannot carry authority over business facts.

## Decision
PostgreSQL is the single system of record for every entity in docs/04. Qdrant holds only derived embeddings and is fully rebuildable. Every Qdrant point stores its source PostgreSQL id, embedding model, model version and content hash; `embedding_index_records` tracks what is indexed so a rebuild is restartable. Losing Qdrant is recoverable without data loss; losing PostgreSQL is not.

## Consequences
- Backups focus on PostgreSQL; Qdrant snapshots are an optimization.
- Changing the embedding model triggers a tracked re-index, not a migration.
- Agents must not treat vector hits as facts; retrieved memories are evidence pointers back to PostgreSQL rows.
