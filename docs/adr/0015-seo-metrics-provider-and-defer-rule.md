# ADR-0015: SEOMetricsProvider abstraction and the DEFER rule

Status: Accepted. Date: 2026-10-16

## Context
The initial policy is DA > 45 and Spam Score < 1, but those are proprietary Moz metrics and no zero-cost legal source has been identified. Scraping Moz or bypassing authentication is prohibited. A missing metric must never be treated as a pass.

## Decision
Qualification depends on a `SEOMetricsProvider` abstraction, not on any vendor. MVP implementations: `MockSEOMetricsProvider` (fixtures) and `ManualImportSEOMetricsProvider` (operator-supplied CSV). A real provider stays TBD and must be legal and ideally zero-cost; any paid provider requires explicit opt-in (`provider_accounts.is_paid`).

If a metric required by campaign policy is unavailable, the prospect becomes **DEFERRED** and is not eligible for outreach. The policy gate returns `METRIC_MISSING`. Never silently pass, never fabricate a value, never infer one from an unrelated signal. A campaign may configure deferral to hard rejection, but never to a pass.

## Consequences
- The pipeline works end-to-end from day one on mock and manual metrics.
- Operators see a visible DEFERRED backlog rather than silent quality erosion.
- Swapping in a real provider is a configuration change, not a refactor.
