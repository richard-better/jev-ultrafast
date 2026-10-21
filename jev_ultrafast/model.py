"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import re
import time

import httpx

from . import tracing
from .questions import NEXT_ACTION, SHORTCUT_VALUES, TARGET, TEXT_VALUE

USER_AGENT = "quietlabs.ai/jev-ultrafast-repl"
CLIENT = httpx.Client(http2=True, timeout=25, headers={"User-Agent": USER_AGENT})
STATES = ("checked", "selected", "expanded", "pressed")
# Failures that can clear on a second try. A bad URL, a redirect loop or an
# undecodable body fails the same way every time.
TRANSIENT_ERRORS = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
RETRY_STATUSES = {429, 503, 504, 529}
# Some JSON-mode models still wrap their object in a markdown fence, optionally tagged json.
FENCED = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def transient_error_body(error):
    """An in-body error that reports a rate limit, overload or gateway timeout."""
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if isinstance(code, str) and code.isdigit():
        code = int(code)
    return code in RETRY_STATUSES or "overloaded" in str(error.get("type", "")).lower()


def post_json(url, key, body, headers=None):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}", **(headers or {})})
        except httpx.HTTPError as error:
            if isinstance(error, TRANSIENT_ERRORS) and attempt < 2:
                tracing.wait("retry", 0.5 * 2**attempt)
                continue
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in RETRY_STATUSES and attempt < 2:
            tracing.wait("retry", 0.5 * 2**attempt)
            continue
        if response.is_error:
            detail = " ".join(response.text.split())[:300]
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed. {detail}")
        try:
            data = response.json()
        except ValueError:
            # A proxy or captive portal answering 200 with HTML parses as a ValueError, which
            # the demo reports as a plain 400. Without this the caller is shown a parser
            # message that says nothing about whether the browser was touched.
            raise RuntimeError("Model provider returned a non-JSON response; no action executed.") from None
        # Some providers and routers answer 200 and report the failure in the body.
        error = data.get("error") if isinstance(data, dict) else None
        if error:
            if transient_error_body(error) and attempt < 2:
                tracing.wait("retry", 0.5 * 2**attempt)
                continue
            message = error.get("message", error) if isinstance(error, dict) else error
            if not isinstance(message, str):
                message = json.dumps(message)
            detail = " ".join(message.split())[:300]
            raise RuntimeError(f"Model provider returned an error; no action executed. {detail}")
        return data
    raise RuntimeError("Model unavailable")


def chat_span(purpose, provider, model):
    return tracing.span(
        "gen_ai.chat",
        **{
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "jev.purpose": purpose,
        },
    )


def record_usage(span, result):
    usage = result.get("usage") if isinstance(result, dict) else None
    if not isinstance(usage, dict):
        return
    # TypeSafe and OpenAI-compatible providers name the same counts differently.
    for key, names in {
        "gen_ai.usage.input_tokens": ("input_tokens", "prompt_tokens"),
        "gen_ai.usage.output_tokens": ("output_tokens", "completion_tokens"),
    }.items():
        value = next((usage[n] for n in names if isinstance(usage.get(n), int)), None)
        if value is not None:
            span.set_attribute(key, value)


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", *STATES) if k in action}
            label = action["label"]
            if kind == "select":
                label = action.get("element_label", label.split(" → ")[0])
            element.update(index=index, label=label, operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", *STATES) if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    # Treat unset, empty and whitespace-only as "use the hosted endpoint": an empty value must not
    # produce a relative URL like "/systemone", which httpx rejects as "Model connection failed".
    base = (os.environ.get("TYPESAFE_BASE_URL", "").strip() or "https://api.typesafe.ai/v1").rstrip("/")
    with chat_span("choose", "typesafe", body["model"]) as span:
        result = post_json(base + "/systemone", os.environ["TYPESAFE_API_KEY"], body)
        record_usage(span, result)
    # A reply can be valid JSON without being an envelope at all: a bare array, string or
    # null answers .get with an AttributeError, which is not what the caller is told to expect.
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict) or "model" not in result:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    answers = result["answers"]
    operation_answer = validate_choice(answers.get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(answers.get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": answers,
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def text_json(system, context, session, purpose):
    """One JSON-mode request to the text model. Returns the parsed reply and request metadata."""
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError(f"{purpose} needs TEXT_MODEL_API_KEY; no value is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    if "opencode.ai/zen/" in base:
        # OpenCode rejects both fields above and accepts only OpenAI's top-level effort.
        reasoning = {"reasoning_effort": os.environ.get("TEXT_MODEL_REASONING") or "low"}
    body = {
        "model": model,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        **reasoning,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": json.dumps(context),
            },
        ],
    }
    started = time.perf_counter()
    with chat_span(purpose.lower(), httpx.URL(base).host, model) as span:
        # OpenCode Go rejects requests without a session ID; it routes and caches by it.
        result = post_json(base + "/chat/completions", key, body, headers={"x-opencode-session": session})
        record_usage(span, result)
    try:
        content = result["choices"][0]["message"]["content"].strip()
        if fenced := FENCED.fullmatch(content):
            content = fenced.group(1)
        output = json.loads(content)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        output = None
    return output, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }


def field_text(context, session):
    output, meta = text_json(TEXT_VALUE, context, session, "TEXT")
    value = output.get("text") if isinstance(output, dict) else None
    if not isinstance(output, dict) or set(output) != {"text"} or not isinstance(value, str) or len(value) > 2000:
        raise ValueError("Text helper returned no valid field value; nothing typed.")
    return value, meta


def shortcut_parameters(context, session):
    """Parameter values for a URL map entry, still unvalidated: url_map.build checks every one."""
    output, meta = text_json(SHORTCUT_VALUES, context, session, "SHORTCUT")
    values = output.get("parameters") if isinstance(output, dict) else None
    if not isinstance(output, dict) or set(output) != {"parameters"} or not isinstance(values, dict):
        raise ValueError("Text helper returned no valid shortcut parameters; nothing opened.")
    return values, meta
