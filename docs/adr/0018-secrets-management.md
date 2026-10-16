# ADR-0018: Secrets management and redaction

Status: Accepted. Date: 2026-10-16

## Context
The system will hold email OAuth tokens or SMTP credentials and possibly provider keys. CLAUDE.md forbids credentials in the repo, logs, fixtures, screenshots, prompts and commits. Agents and subprocesses must see as little as possible.

## Decision
- **Production:** secrets live in the OS secure store via the cross-platform `keyring` library. The database stores only a `secret_ref` (key name) in `provider_accounts`, never the secret.
- **Development:** environment variables from `.env`, which is git-ignored; only `.env.example` (placeholders) is committed.
- A `SecretStore` provider abstracts both, so no module reads credentials directly.
- Structured logs pass a redaction filter covering tokens, Authorization headers, cookies, passwords, API keys and the per-launch API token (contract C13).
- Secrets never enter prompts, agent context, `tool_calls.args_redacted`, fixtures, error payloads or the renderer.
- CI runs a secret scan (gitleaks); the Foundation Gate requires it clean.

## Consequences
- First-run setup must guide the operator through credential entry into the keyring.
- Headless CI uses mock providers only, since no keyring is available.
- Diagnostic bundles must be redacted before export.
