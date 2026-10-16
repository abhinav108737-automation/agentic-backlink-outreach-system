#!/usr/bin/env bash
# Contract drift gate: OpenAPI snapshot == app, and generated TS == snapshot.
set -euo pipefail
cd "$(dirname "$0")/../.."
uv run python -m kb_api.export_openapi --check
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
npm run --silent generate:to -w @ken/contracts -- "$tmp" >/dev/null
if ! diff -q "$tmp" contracts/generated_ts/src/api.d.ts >/dev/null; then
  echo "Generated TS client drift: run \`npm run gen:contracts\`" >&2; exit 1
fi
echo "Contracts in sync (OpenAPI + generated TS)"
