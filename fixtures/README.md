# Fixture formats (contract C12)

Lane E owns the corpus; formats are frozen here. All fixtures are synthetic: no real credentials,
no real personal data. Directory-per-kind, UTF-8, deterministic.

| Kind | Path | Format |
|---|---|---|
| SERP result set | `fixtures/serp/<query-slug>.json` | `{"query": str, "results": [SearchResult]}` (kb_contracts.providers) |
| HTML site bundle | `fixtures/sites/<domain>/` | `site.json` (`{"domain", "pages": {"<path>": "<file>"}, "labels": {...}}`) + one `.html` per page; `labels` marks eligible / rejected / spammy / missing_metrics / prompt_injection |
| SEO metrics | `fixtures/metrics/<name>.csv` | header `domain,metric,value,source,fetched_at`; blank `value` = unavailable |
| Email thread | `fixtures/email/threads/<id>.json` | `{"thread_ref", "messages": [InboundEmail \| OutboundEmail]}` |
| Reply scenario | `fixtures/email/scenarios/<id>.json` | `{"trigger": {"sequence_step": int}, "after_seconds": int, "reply": InboundEmail, "expected_class": ReplyClass}` |

Prompt-injection fixtures (pages and emails) are mandatory per ADR-0020 and carry
`"labels": {"prompt_injection": true}` so tests can assert no policy/scope/state change occurs.
