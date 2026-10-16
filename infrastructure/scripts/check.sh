#!/usr/bin/env bash
# Foundation Gate: every local quality gate in one command.
#   check.sh            full run (starts the dev compose stack if needed)
#   check.sh --ci       CI mode (services provided by the runner; npm ci)
#   check.sh --smoke    also launch real Electron and verify handshake + renderer hardening
set -euo pipefail
cd "$(dirname "$0")/../.."
CI=0; SMOKE=0
for a in "$@"; do case "$a" in --ci) CI=1;; --smoke) SMOKE=1;; *) echo "unknown arg $a"; exit 2;; esac; done
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# DB-backed tests must never be silently skipped inside the gate.
export KB_REQUIRE_DB=1
# --frozen: the gate fails on lockfile drift instead of quietly re-resolving.
step "Python deps (uv, Python 3.12, frozen lockfile)"; uv sync --frozen
step "ruff check";                      uv run ruff check .
step "ruff format --check";             uv run ruff format --check .
step "mypy (strict on kb_*)";           uv run mypy contracts/python/src apps/api/src services agents
step "import-linter (boundaries)";      uv run lint-imports

if [ "$CI" -eq 0 ]; then
  step "dev stack (PostgreSQL 55432, Qdrant 56333)"
  docker compose -f infrastructure/docker/compose.dev.yml up -d --wait postgres qdrant
fi

step "pytest (contract, security, migrations, handshake)"; uv run pytest -q
step "requirements registry";            uv run python infrastructure/scripts/check_registry.py

step "JS deps";                          if [ "$CI" -eq 1 ] || [ ! -d node_modules ]; then npm ci; else echo "node_modules present"; fi
step "eslint";                           npm run lint
step "tsc (contracts + desktop)";        npm run typecheck
step "vitest (bridge, security, backend proxy)"; npm test
step "contract drift (OpenAPI + TS)";    infrastructure/scripts/check-contracts.sh
step "secret scan";                      infrastructure/scripts/secret-scan.sh

if [ "$SMOKE" -eq 1 ]; then
  step "electron smoke (real app)"
  npm run smoke -w @ken/desktop 2>&1 | grep -E '^(SMOKE_|backend failed)' | tee /dev/stderr | grep -q '^SMOKE_OK$'
fi
printf '\n\033[1;32mFoundation gate: ALL CHECKS PASSED\033[0m\n'
