# Deployment & Local Operations

Target: standalone Electron desktop application with local backend.
Runtime: Electron/React, FastAPI, PostgreSQL, Qdrant, Ollama, worker/scheduler, local reports/files.
Development: Docker Compose for PostgreSQL/Qdrant; Ollama may run on host; Electron/FastAPI run on host.
Production: packaged Electron installer + local backend packaging strategy validated by a dedicated spike; local data directory; health checks; backup/export.
First run: DB migrations, Qdrant bootstrap, Ollama health/model check, email setup, search/crawl setup, SEO metrics setup, shadow/mock default, explicit real-send enablement.
Logs: local structured, rotated, redacted, exportable diagnostic bundle.
Backups: PostgreSQL authoritative; Qdrant rebuildable.
