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

## Email fixtures (Wave 2A)

- `scenarios/<id>.json` follows the Reply scenario format above, plus an optional top-level
  `"labels"` object (`{"prompt_injection": true}` on injection files). Inside `reply`, the literals
  `{recipient}` and `{thread_ref}` are substituted textually by `MockEmailProvider`; the reply is
  derived from the send time (`after_seconds`) and `received_at` in the file is only a placeholder.
  `trigger.sequence_step` is the n-th distinct message sent to the scripted recipient.
- Corpus: positive, question, negotiation (fee, binding), negative, opt-out, out-of-office (with a
  return date), bounce (DSN from `mailer-daemon@`), unknown, and `prompt-injection-positive`.
  Every `expected_class` is asserted by the deterministic classifier tests.
- `threads/<id>.json` holds typed multi-message conversations (`from_address` marks an inbound
  message, `to` an outbound one). Addresses use reserved example/test domains only.
- `sites/publisher-verification-lab.net/` is a set of *published article* pages (good link,
  nofollow, changed anchor, removed link, foreign canonical, hidden link, comment injection) used
  by backlink verification. It is labelled `backlink_verification`, not a prospect.
