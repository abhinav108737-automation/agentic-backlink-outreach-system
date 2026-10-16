# Security, Governance & Outreach Safety

Secrets: never commit credentials; prefer OS secure storage; redact logs; minimize token exposure to LLMs/subprocesses.

Email: real-send disabled by default; sender setup required; per-campaign/global limits; suppression; opt-out; bounce; duplicate prevention; idempotency; kill switch; audit; configurable human review for sensitive negotiation.

Browser/crawler: approved public pages only; rate limits; browser fallback after deterministic crawl; no private session-cookie scraping or authentication bypass.

Agents never get unrestricted runtime shell, raw DB mutation, unguarded email send, policy override, or audit deletion.

Learning: no autonomous promotion of high-risk policy changes. Every promotion records evidence, evaluator, versions and rollback.

Autonomy and approvals (ADR-0007): SHADOW during development; GUARDED for the first real-email release. Human approval is required for first-time live-send activation, high-risk negotiation commitments and L3 policy changes. Routine follow-ups become configurable only after policy/evaluation gates are proven. A global kill switch blocks all sends.

Localhost API (ADR-0014): FastAPI binds 127.0.0.1 only on a dynamically selected port. Electron main generates a per-launch 256-bit token, passes it to the backend over stdin, and proxies renderer requests through IPC, attaching the token itself, so the renderer never holds it. Every privileged request is authenticated with a constant-time comparison. CORS denies all origins; a Host allowlist blocks DNS rebinding; the token is never persisted or logged.

SEO metrics (ADR-0015): no scraping of Moz or any authenticated source. A prospect missing a required metric is DEFERRED and not eligible for outreach.

Untrusted content (ADR-0020): crawled pages, search results and email bodies are data, never instructions. They are delimited as quoted evidence in prompts, cannot change tool scope, and are covered by prompt-injection fixtures and tests.
