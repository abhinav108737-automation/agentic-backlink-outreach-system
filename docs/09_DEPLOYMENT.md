# Deployment & Local Operations

Target: standalone Electron desktop application with local backend.
Runtime: Electron/React, FastAPI, PostgreSQL, Qdrant, Ollama, in-process worker with a PostgreSQL-backed job queue (ADR-0008; no Redis/Celery), local reports/files.
Development: Docker Compose for PostgreSQL (host port 55432) and Qdrant (6333); Ollama on host; Electron main spawns the FastAPI backend on a loopback dynamic port (ADR-0014).
Production: packaged Electron installer + local backend packaging strategy validated by a dedicated spike (ADR-0019); local data directory; health checks; backup/export.
Packaging order: macOS is the first packaging/smoke-test target (development machine). Architecture stays cross-platform; no macOS-specific decisions. Windows packaging is required before production rollout if Ken Research end users operate primarily on Windows.
First run: DB migrations, Qdrant bootstrap, Ollama health/model check, email setup, search/crawl setup, SEO metrics setup, shadow/mock default, explicit real-send enablement.
Logs: local structured, rotated, redacted, exportable diagnostic bundle.
Backups: PostgreSQL authoritative; Qdrant rebuildable.
