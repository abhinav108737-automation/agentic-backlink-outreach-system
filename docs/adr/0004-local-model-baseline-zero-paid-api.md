# ADR-0004: Local-model baseline and zero paid AI API

Status: Accepted. Date: 2026-10-16

## Context
CLAUDE.md sets an incremental paid AI API budget of zero. Consumer subscriptions (ChatGPT Plus, Claude Pro) do not grant API rights, and automating them through cookie or session scraping is prohibited.

## Decision
Ollama-compatible local inference is the mandatory functional baseline; the full MVP and V1 acceptance paths run with local models only. Consumer-subscription browser/OAuth access is **not** a v1 dependency and is not implemented. `ModelProvider` stays extensible so an officially supported, authenticated provider can be added later behind a feature flag and an explicit `is_paid` opt-in. No paid provider is ever enabled silently.

Initial spike models (configurable, not yet defaults): `qwen3:8b-q4_K_M` for reasoning and structured output, `nomic-embed-text` for embeddings. Both are evaluated (structured-output validity, classification quality, latency) before either becomes the production default; that evaluation is recorded as an addendum here.

## Consequences
- Prompts and schemas must be sized for an 8B-class local model; large-context strategies are needed for research synthesis.
- CI uses TestModel/FunctionModel and never requires Ollama.
- Quality ceilings are accepted in exchange for zero marginal cost and local privacy.
- Human review is the escalation path when the local model is unavailable or ambiguous.
