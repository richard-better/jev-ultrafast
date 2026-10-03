# Jev Ultrafast

Read README.md before editing. Keep the loop small: page -> indexed elements -> operation + target -> execution.

- The input is one natural-language goal. Do not add site-specific plans or hardcoded field values to the loop, prompts, or core code.
- We are experimenting with URL maps: loaded data files of URL patterns for sites where a pattern makes sense, such as a search that a URL can express. A map cuts the page work the agent has to do and scales across sites without site-specific code. The model only chooses a map entry; typed, validated parameters fill it, and code builds the URL on the entry's own site. The model never writes a URL.
- Be honest in code, docs, and evidence. Never add workarounds to the core or the product to make a task, check, or benchmark pass; fix the real cause, or report the gap.
- TypeSafe chooses an operation and operation-specific target heads in one request. Consume only the selected operation's target.
- Targets must map to observed elements and supported operations. Never let the model emit selectors or executable code.
- TYPE_TEXT invokes the text LLM. Cache a stale retry's value only while its entire helper input is identical and it targets the same element (compare the action's code-owned node identity).
- Never retry a browser mutation. Log execution before observing its result.
- If execution is interrupted without a pre-input stale rejection, stop the run and preserve an unknown execution record. Inspect the page before starting over; a missing reply is not evidence of no side effect.
- Type only into the observed field, or an editor its click opened that holds focus under the clicked point. If the click moved focus anywhere else before any key, log a confirmed click with no text and continue. Focus lost after a key is unknown: stop.
- Interruption checks must require stopped commands to reject explicitly. Error UI must distinguish a new unknown execution from an older stopped run retained after a failed reset.
- Screenshots are optional; the model does not consume them. Keep demonstration footage at its original speed.
- Keep credentials server-side and .env ignored. Tests must not call paid APIs.
- Verify actual final outcomes independently. A DONE choice is not proof of success.
- Keep examples, README claims, raw evidence, and model-call counts consistent.
- Do not commit or push unless the user requests it.

Checks: uv run ruff check ., uv run pytest, node --check jev_ultrafast/static/app.js, uv build.
Execution interruption checks: node --test tests/test_interrupted_ui.cjs and uv run python scripts/check_interrupted_actions.py (local browser, no model calls).
