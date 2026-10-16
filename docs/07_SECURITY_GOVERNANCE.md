# Security, Governance & Outreach Safety

Secrets: never commit credentials; prefer OS secure storage; redact logs; minimize token exposure to LLMs/subprocesses.

Email: real-send disabled by default; sender setup required; per-campaign/global limits; suppression; opt-out; bounce; duplicate prevention; idempotency; kill switch; audit; configurable human review for sensitive negotiation.

Browser/crawler: approved public pages only; rate limits; browser fallback after deterministic crawl; no private session-cookie scraping or authentication bypass.

Agents never get unrestricted runtime shell, raw DB mutation, unguarded email send, policy override, or audit deletion.

Learning: no autonomous promotion of high-risk policy changes. Every promotion records evidence, evaluator, versions and rollback.
