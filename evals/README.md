# Agent evals

- `injection_eval.py` runs the Research, Contact and Qualification agents over the
  `prompt_injection`-labelled fixture sites with a "compliant" and an "obedient" fake model and
  reports invariant violations (tool calls, scope, prompts, structured output).
  `tests/unit/agents/test_prompt_injection_eval.py` asserts the report is empty.
- `cases/prompt_injection.json` holds the case list and the forbidden markers.

Fully offline: fake model, fixture crawler, in-memory capabilities. Run:
`uv run pytest tests/unit/agents -q` (or `uv run python evals/injection_eval.py`).
