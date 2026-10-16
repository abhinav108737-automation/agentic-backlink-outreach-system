# ADR-0017: Infrastructure minimalism

Status: Accepted. Date: 2026-10-16

## Context
Reference material from a different project (Internal Linking) showed Redis, Celery, Elasticsearch, S3, Django and LangGraph. Copying that stack would add operational weight with no requirement behind it, and every extra service must be installed, supervised, packaged and backed up inside a desktop application.

## Decision
No Redis, Celery, LangGraph, Elasticsearch, S3, Kubernetes or comparable component is added without a documented requirement and an architecture review recorded as an ADR. The v1 runtime is Electron, FastAPI, PostgreSQL, Qdrant and Ollama, with an in-process worker on a PostgreSQL job queue (ADR-0008). Queueing uses PostgreSQL; search over business data uses PostgreSQL; semantic retrieval uses Qdrant; file storage is the local data directory. PydanticAI remains the runtime Agentic Operator.

## Consequences
- Packaging and first-run setup stay tractable.
- Some scale ceilings are accepted; desktop workloads are far below them.
- A future component must prove a measured need, not an architectural fashion.
