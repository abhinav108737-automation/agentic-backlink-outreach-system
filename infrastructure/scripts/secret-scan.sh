#!/usr/bin/env bash
# Secret scan over tracked + untracked-but-not-ignored files. Uses gitleaks when installed,
# otherwise a built-in pattern scan (reported as such so the gate never overstates itself).
set -euo pipefail
cd "$(dirname "$0")/../.."
if command -v gitleaks >/dev/null 2>&1; then
  # Two passes: commit history (a secret removed in a later commit is still leaked) and the
  # current working tree (uncommitted files never reach the history pass). Scope is narrowed
  # by .gitleaks.toml; the upstream ruleset itself is used unmodified.
  gitleaks git --no-banner --redact . || exit 1
  gitleaks dir --no-banner --redact . || exit 1
  echo "secret scan: gitleaks clean (history + working tree), $(gitleaks version)"
  exit 0
fi
# Fallback only: weaker than gitleaks, and the Foundation gate records which engine ran.
echo "secret scan: gitleaks not installed -> using built-in pattern fallback"
pat='-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[abprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{32,}|AIza[0-9A-Za-z_-]{35}|(api[_-]?key|secret|passwd|password|token)["'"'"' ]*[:=]["'"'"' ]*[A-Za-z0-9/+_-]{24,}'
files=$(git ls-files -co --exclude-standard | grep -vE '(^|/)(package-lock\.json|uv\.lock|\.claude/|docs/reference/|contracts/generated_ts/src/api\.d\.ts)' || true)
# Known-safe: dev-only compose placeholder credentials and the tests' synthetic token.
hits=$(echo "$files" | xargs grep -InE "$pat" 2>/dev/null | grep -vE 'tests/|scripts/secret-scan.sh|POSTGRES_PASSWORD: backlink|postgresql\+(asyncpg|psycopg)://backlink:backlink@' || true)
if [ -n "$hits" ]; then echo "$hits" | cut -c1-160; echo "secret scan: POTENTIAL SECRETS FOUND" >&2; exit 1; fi
echo "secret scan: clean (fallback patterns)"
