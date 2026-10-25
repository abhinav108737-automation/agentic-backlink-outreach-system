# Agent evals

- `injection_eval.py` runs the Research, Contact and Qualification agents over the
  `prompt_injection`-labelled fixture sites with a "compliant" and an "obedient" fake model and
  reports invariant violations (tool calls, scope, prompts, structured output).
  `tests/unit/agents/test_prompt_injection_eval.py` asserts the report is empty.
- `cases/prompt_injection.json` holds the case list and the forbidden markers.

Fully offline: fake model, fixture crawler, in-memory capabilities. Run:
`uv run pytest tests/unit/agents -q` (or `uv run python evals/injection_eval.py`).
- `reply_injection_eval.py` + `cases/reply_injection.json` do the same for the Reply and
  Negotiation agents over `fixtures/email/scenarios/prompt-injection-positive.json` and inline
  injected reply bodies (opt-out, bounce, subject, delimiter breakout, commitment). Three scripted
  models run per case: `compliant`, `obedient` (hijacked only if it sees injected text) and
  `hijacked` (always attacks). Asserted: tool calls and scope unchanged, the deterministic class
  (real `classify_reply`) unchanged, a hijacked re-classification FAILS the run, a hijacked
  "not binding" negotiation draft is overruled to binding and `needs_human`, and no injected
  marker reaches a model request, the structured result, the run store or the tool-call audit.
  `tests/unit/agents/test_reply_injection_eval.py` asserts the reports are empty (and, by
  disabling the sanitiser, that the eval is not vacuous).
  Run: `PYTHONPATH=evals uv run python evals/reply_injection_eval.py`.
