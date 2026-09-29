"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_nested_scroll_region_is_a_named_operation_control():
    p = page()
    p["actions"].append({
        "id": "scroll_region_down_1", "kind": "scroll", "label": "Scroll down Search results",
        "node": 30, "delta": 420, "scroll_top": 0, "scroll_height": 900, "client_height": 240,
    })
    _elements, _targets, controls = model.action_space(p["actions"])
    assert controls["SCROLL_REGION_DOWN_1"]["node"] == 30


@pytest.mark.parametrize("kind,operation,role", [("click", "CLICK", "button"), ("fill", "TYPE_TEXT", "textbox")])
def test_element_labels_preserve_literal_arrows(kind, operation, role):
    labels = ["Move → Inbox", "Move → Archive", "Cancel"]
    actions = [
        {"id": f"e{i}", "node": i, "kind": kind, "role": role, "label": label, "value": ""}
        for i, label in enumerate(labels, 1)
    ]
    original = deepcopy(actions)
    elements, targets, _ = model.action_space(actions)
    assert [element["label"] for element in elements] == labels
    assert targets[operation] == {str(i): action for i, action in enumerate(actions, 1)}
    assert actions == original


@pytest.mark.parametrize("explicit_label", [False, True])
def test_select_element_name_is_separate_from_option_labels(explicit_label):
    label = "Delivery → Method" if explicit_label else "Delivery"
    actions = [
        {"id": f"e{i}", "node": 1, "kind": "select", "role": "combobox", "value": value,
         "current_value": "Current", "label": f"{label} → {option}",
         **({"element_label": label} if explicit_label else {})}
        for i, (value, option) in enumerate([("archive", "Inbox → Archive"), ("trash", "Inbox → Trash")], 1)
    ]
    original = deepcopy(actions)
    elements, targets, _ = model.action_space(actions)
    assert elements[0]["label"] == label
    assert elements[0]["value"] == "Current"
    assert elements[0]["options"] == [
        {"index": f"1:{i}", "label": action["label"], "value": action["value"]}
        for i, action in enumerate(actions, 1)
    ]
    assert targets["SELECT"] == {f"1:{i}": action for i, action in enumerate(actions, 1)}
    assert actions == original


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_toggle_button_pressed_state_reaches_the_model(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Nonstop only", "node": 30, "role": "button", "pressed": "true",
    })
    sent = []

    def post(_url, _key, body):
        sent.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "click_target": choice(body["questions"]["click_target"]["criteria"], "1"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(p, "Show nonstop flights", [])
    assert sent[0]["state"]["elements"][0]["pressed"] == "true"
    assert sent[0]["questions"]["click_target"]["criteria"]["1"]["pressed"] == "true"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.max_steps = 60
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


@pytest.mark.parametrize("error_type", [TimeoutError, RuntimeError, KeyboardInterrupt])
def test_interrupted_browser_action_stops_the_run_without_replaying(runner, monkeypatch, error_type):
    runner.state["decision"] = decision("e3")
    choose = Mock(return_value=decision("e3"))
    monkeypatch.setattr(loop, "choose", choose)
    effects = []

    def act(*_args, **_kwargs):
        effects.append("submitted")
        raise error_type("The reply was lost after input")

    runner.state["browser"].act.side_effect = act
    with pytest.raises(error_type, match="reply was lost"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})

    assert runner.snapshot()["status"] == "blocked"
    assert runner.snapshot()["history"][-1]["execution"] == "unknown"
    assert runner.snapshot()["history"][-1]["page_changed"] is None
    assert runner.snapshot()["history"][-1]["executed_ms"] is None
    assert list(runner.run()) == []
    # Even navigation during the next freshness read must not reopen a stopped run.
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    with pytest.raises(ValueError, match="stopped"):
        runner.command("tick")
    runner.state["browser"].fresh.assert_not_called()
    choose.assert_not_called()
    assert effects == ["submitted"]
    runner.state["browser"].observe.assert_not_called()


def test_interrupted_fill_discards_generated_text(runner, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(return_value=("book", {"model": "test", "latency_ms": 10})))
    runner.state["browser"].act.side_effect = TimeoutError("Input reply lost")
    with pytest.raises(TimeoutError):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.pending_text is None
    assert runner.state["history"][-1]["text"] == "book"
    assert runner.state["status"] == "blocked"


@pytest.mark.parametrize("text", ["book", ""])
def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch, text):
    helper = Mock(return_value=(text, {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


@pytest.mark.parametrize("text", ["", "   ", "London", "  London  "])
def test_generated_text_reaches_execution_and_history_unchanged(runner, monkeypatch, text):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": json.dumps({"text": text})}}]})
    monkeypatch.setattr(model, "post_json", post)
    p = runner.state["page"]
    for action in p["actions"]:
        if action.get("node") == 10:
            action["value"] = "Paris"
    p["fingerprint"] = fingerprint(p)

    runner.command("act", {"fingerprint": p["fingerprint"]})

    runner.state["browser"].act.assert_called_once_with(p["actions"][0], p, text=text)
    assert runner.state["history"][-1]["text"] == text
    assert runner.state["text_calls"][-1]["value"] == text
    assert runner.pending_text is None
    assert post.call_count == 1


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_screenshot_timeout_preserves_structured_observation(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()

    def cdp(method, **_kwargs):
        if method == "Runtime.evaluate":
            return {"result": {"value": p}}
        if method == "Page.captureScreenshot":
            raise TimeoutError("Page.captureScreenshot timed out")
        raise AssertionError(method)

    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": True})
    assert actual["actions"] == p["actions"]
    assert actual["screenshot"] is None


def test_recording_skips_frames_when_screenshot_times_out(monkeypatch, tmp_path):
    initial = page()
    initial["screenshot"] = None
    after_action = deepcopy(initial)
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(side_effect=[initial, after_action]))
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))

    agent = loop.Agent("https://example.test/", "Click Go", record_dir=tmp_path)
    agent.state["decision"] = decision("e3")
    agent.state["decision"]["operation"] = "CLICK"
    agent.state["status"] = "predicted"
    agent.state["started_at"] = time.perf_counter()
    agent.command("act", {"fingerprint": initial["fingerprint"]})
    agent.close()

    assert list(tmp_path.glob("*.jpg")) == []
    assert agent.state["history"][-1]["action"] == "Go"
    assert browser.observe.call_count == 2
    assert browser.close.call_count == 1


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize(("action_id", "delta"), [
    ("scroll_region_down_1", 420),
    ("scroll_region_up_1", -120),
])
def test_executor_scrolls_an_observed_region_at_a_safe_sample(monkeypatch, action_id, delta):
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, **params):
        calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": {"x": 120, "y": 340}}}
        return {}

    monkeypatch.setattr(browser, "cdp", cdp)
    browser_operation({
        "operation": "act",
        "session": "test",
        "action": {
            "id": action_id,
            "kind": "scroll",
            "node": 7,
            "delta": delta,
            "scroll_top": 0,
            "scroll_height": 900,
            "client_height": 240,
        },
    })
    assert calls[-1] == (
        "Input.dispatchMouseEvent",
        {
            "session_id": "test",
            "type": "mouseWheel",
            "x": 120,
            "y": 340,
            "deltaX": 0,
            "deltaY": delta,
        },
    )


def test_executor_preserves_page_scroll_coordinates(monkeypatch):
    import jev_ultrafast.browser as browser

    cdp = Mock(return_value={})
    monkeypatch.setattr(browser, "cdp", cdp)
    browser_operation({
        "operation": "act",
        "session": "test",
        "action": {"id": "scroll_down", "kind": "scroll", "delta": 560},
    })
    cdp.assert_called_once_with(
        "Input.dispatchMouseEvent",
        session_id="test",
        type="mouseWheel",
        x=550,
        y=650,
        deltaX=0,
        deltaY=560,
    )


def test_nested_scroll_freshness_binds_to_the_observed_region_state():
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.evaluate = Mock(return_value=[["document"], [7, 0, 900, 240]])
    observed = {"page_key": ["document"]}
    action = {
        "kind": "scroll",
        "node": 7,
        "scroll_top": 0,
        "scroll_height": 900,
        "client_height": 240,
    }
    assert b.fresh(observed, action)
    b.evaluate.return_value = [["document"], [7, 120, 900, 240]]
    assert not b.fresh(observed, action)


def test_executor_rejects_a_stale_nested_scroll_region(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, **params):
        calls.append((method, params))
        return {"result": {"value": None}}

    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(StalePage, match="Scrollable region changed or is covered"):
        browser_operation({
            "operation": "act",
            "session": "test",
            "action": {
                "id": "scroll_region_down_1",
                "kind": "scroll",
                "node": 7,
                "delta": 420,
                "scroll_top": 0,
                "scroll_height": 900,
                "client_height": 240,
            },
        })
    assert not any(method == "Input.dispatchMouseEvent" for method, _params in calls)


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "option_node": 2, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


def test_fingerprint_ignores_geometry():
    p = page()
    p["actions"][0]["rect"] = {"x": 10.0, "y": 20.0, "w": 120.0, "h": 32.0}
    other = deepcopy(p)
    other["actions"][0]["rect"] = {"x": 18.0, "y": 24.0, "w": 120.0, "h": 32.0}
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["value"] = "London"
    assert fingerprint(p) != fingerprint(other)


def test_fingerprint_ignores_document_height_but_not_scroll_position():
    p = page()
    p["scroll"] = {"y": 0, "height": 2400}
    other = deepcopy(p)
    other["scroll"]["height"] = 2600
    assert fingerprint(p) == fingerprint(other)
    other["scroll"]["y"] = 560
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import DEPARTURE, verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": f"Track prices from Zürich to London departing {DEPARTURE.iso}",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", DEPARTURE.short_weekday),
                (f"Nonstop flight on {DEPARTURE.long_weekday}. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        wrong_year = str(DEPARTURE.date.year + 1)
        actual["text"] = actual["text"].replace(str(DEPARTURE.date.year), wrong_year)
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", [
        "Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}',
        '{"text":false}', '{"text":0}', '{}', json.dumps({"text": "a" * 2001}),
    ]
)
def test_text_helper_rejects_invalid_values(runner, monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert not runner.state["history"]


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, "2"])
def test_invalid_budget_cannot_open_browser(monkeypatch, budget):
    browser = Mock()
    monkeypatch.setattr(loop, "Browser", browser)
    with pytest.raises(ValueError, match="max_steps"):
        loop.Agent("https://example.test", "Search", max_steps=budget)
    browser.assert_not_called()


def test_action_budget_prevents_mutation(runner):
    runner.max_steps = 1
    runner.state["history"] = [{"action": "previous"}]
    with pytest.raises(ValueError, match="1-action budget"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()


def test_stale_decisions_have_bounded_model_calls(runner, monkeypatch):
    runner.max_steps = 1
    runner.state["decisions"] = [{}, {}]
    choose = Mock()
    monkeypatch.setattr(loop, "choose", choose)
    with pytest.raises(ValueError, match="model-call budget"):
        runner.command("predict")
    choose.assert_not_called()


def test_required_daemon_never_falls_back_to_another_browser(monkeypatch):
    from jev_ultrafast import browser

    monkeypatch.setenv("BH_REQUIRE_EXISTING_DAEMON", "1")
    monkeypatch.setattr(browser, "daemon_browser_ready", Mock(return_value=False))
    ensure = Mock()
    cdp = Mock()
    monkeypatch.setattr(browser, "ensure_daemon", ensure)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="required Browser Harness daemon"):
        browser.Browser("https://example.test")
    ensure.assert_not_called()
    cdp.assert_not_called()


def test_scroll_uses_current_small_viewport(monkeypatch):
    from jev_ultrafast import browser

    cdp = Mock(side_effect=[{"result": {"value": {"width": 360, "height": 400}}}, {}])
    monkeypatch.setattr(browser, "cdp", cdp)
    browser.browser_operation({"operation": "act", "session": "test",
                               "action": {"id": "scroll_down", "kind": "scroll", "delta": 560}})
    assert cdp.call_args.args == ("Input.dispatchMouseEvent",)
    assert cdp.call_args.kwargs["x"] == 180 and cdp.call_args.kwargs["y"] == 200


def test_observation_waits_for_navigation_without_repeating_input(monkeypatch):
    from jev_ultrafast import browser

    b = browser.Browser.__new__(browser.Browser)
    b.session = "test"
    operation = Mock(side_effect=[StalePage("navigating")] * 8 + [page()])
    sleep = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    monkeypatch.setattr(browser.time, "sleep", sleep)
    assert b.observe(screenshot=False)["url"] == "https://example.test/"
    assert operation.call_count == 9
    assert all(call.args[0]["operation"] == "observe" for call in operation.call_args_list)
    assert sum(call.args[0] for call in sleep.call_args_list) >= 1.5


def test_remote_response_deadline_does_not_retry_mutations(monkeypatch):
    from jev_ultrafast import browser

    b = browser.Browser.__new__(browser.Browser)
    b.session = "test"
    cdp = Mock(side_effect=TimeoutError("unknown navigation result"))
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(TimeoutError):
        b.call("Page.navigate", url="https://example.test")
    cdp.assert_called_once_with("Page.navigate", session_id="test", _response_timeout=30,
                                url="https://example.test")


def test_optional_screenshot_keeps_its_short_deadline(monkeypatch):
    from jev_ultrafast import browser

    b = browser.Browser.__new__(browser.Browser)
    b.session = "test"
    cdp = Mock(return_value={"data": "image"})
    monkeypatch.setattr(browser, "cdp", cdp)
    b.call("Page.captureScreenshot", format="jpeg")
    cdp.assert_called_once_with("Page.captureScreenshot", session_id="test", _response_timeout=5, format="jpeg")


def test_run_yields_independent_snapshots(runner, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(side_effect=[decision("wait"), decision("wait"), decision("DONE")]))

    states = list(runner.run())

    assert [len(state["history"]) for state in states] == [1, 2, 2]
    assert [len(state["decisions"]) for state in states] == [1, 2, 3]
    assert [state["status"] for state in states] == ["ready", "ready", "done"]
    assert all("browser" not in state for state in states)
    json.dumps(states)


def test_editing_a_snapshot_cannot_change_the_agent(runner):
    runner.state["plan"] = ["Find a book"]
    runner.state["text_calls"] = [{"usage": {"total_tokens": 12}}]
    expected = deepcopy({key: value for key, value in runner.state.items() if key != "browser"})

    snapshot = runner.snapshot()
    snapshot["page"]["actions"][0]["node"] = 999
    snapshot["decision"]["probabilities"]["e1"] = 0
    snapshot["history"].append({"action": "external annotation"})
    snapshot["plan"].clear()
    snapshot["text_calls"][0]["usage"]["total_tokens"] = 0

    assert {key: value for key, value in runner.state.items() if key != "browser"} == expected


@pytest.mark.parametrize("action", ["wait", "DONE", "stale"])
def test_tick_exports_only_one_snapshot(runner, monkeypatch, action):
    choice = "wait" if action == "stale" else action
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision(choice)))
    if action == "stale":
        runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    snapshot = Mock(wraps=runner.snapshot)
    monkeypatch.setattr(runner, "snapshot", snapshot)

    result = runner.command("tick")

    snapshot.assert_called_once_with()
    assert result["status"] == ("done" if action == "DONE" else "ready")


def test_inspector_reuses_the_command_snapshot(runner, monkeypatch):
    from jev_ultrafast import demo

    monkeypatch.setattr(loop, "choose", Mock(return_value=decision("wait")))
    monkeypatch.setattr(demo, "AGENT", runner)
    snapshot = Mock(wraps=runner.snapshot)
    monkeypatch.setattr(runner, "snapshot", snapshot)

    result = demo.command("tick", {})

    snapshot.assert_called_once_with()
    assert result["status"] == "ready"
    assert len(result["history"]) == 1
    assert result["max_steps"] == loop.MAX_STEPS


def test_only_proven_progress_resets_the_no_progress_stop():
    """Only page_changed=True counts as progress; False and None do not."""
    assert loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": False, "kind": "click"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert not loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": True, "kind": "click"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": None, "kind": "click"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert not loop.stalled(
        [
            {"page_changed": False, "kind": "click"},
            {"page_changed": False, "kind": "wait"},
            {"page_changed": False, "kind": "click"},
        ]
    )
    assert not loop.stalled([{"page_changed": False, "kind": "click"}])


def test_alternating_failed_observations_still_block_a_stalled_run(runner):
    """Regression test for https://github.com/browser-use/jev-ultrafast/issues/94.

    A failed post-action observation leaves page_changed=None. Alternating
    None with False must still trip the three-repeat no-progress guard
    instead of letting a stuck run spend the whole model-call budget.
    """
    current = runner.state["page"]
    runner.state["browser"].observe.side_effect = [current, StalePage("changed"), current]
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        try:
            runner.command("act", {"fingerprint": current["fingerprint"]})
        except StalePage:
            pass
    assert [entry["page_changed"] for entry in runner.state["history"]] == [False, None, False]
    assert runner.state["status"] == "blocked"


def test_stale_recovery_applies_the_no_progress_stop(runner):
    """The tick StalePage recovery must not revive a stalled run as ready."""
    runner.state["history"] = [
        {"page_changed": False, "kind": "click"},
        {"page_changed": None, "kind": "click"},
        {"page_changed": False, "kind": "click"},
    ]
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


def test_failed_third_observation_still_blocks_a_stalled_run(runner):
    """The act except-path guard: when the third post-action observation
    itself fails, the run must still stop instead of staying ready."""
    current = runner.state["page"]
    runner.state["browser"].observe.side_effect = [current, current, StalePage("changed")]
    for _ in range(3):
        runner.state["decision"] = decision("e3")
        try:
            runner.command("act", {"fingerprint": current["fingerprint"]})
        except StalePage:
            pass
    assert [entry["page_changed"] for entry in runner.state["history"]] == [False, False, None]
    assert runner.state["status"] == "blocked"


def test_tick_recovery_never_revives_a_stalled_run(runner):
    """A failed recovery observation must not erase the stalled result:
    tick returns blocked without re-enabling decisions."""
    runner.state["history"] = [
        {"page_changed": False, "kind": "click"},
        {"page_changed": None, "kind": "click"},
        {"page_changed": False, "kind": "click"},
    ]
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.state["browser"].observe.side_effect = StalePage("still navigating")
    runner.command("tick")
    assert runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


def provider(monkeypatch, response=None, error=None):
    """Answer one model call with a chosen payload, without touching the network."""
    reply = Mock(status_code=200, is_error=False)
    reply.json = Mock(side_effect=error) if error else Mock(return_value=response)
    monkeypatch.setattr(model, "CLIENT", Mock(post=Mock(return_value=reply)))


def test_a_non_json_provider_reply_reports_that_nothing_ran(monkeypatch):
    """The parser error is replaced by one that states no browser action was executed."""
    provider(monkeypatch, error=json.JSONDecodeError("Expecting value", "<html>", 0))
    with pytest.raises(RuntimeError, match="no action executed"):
        model.post_json("https://provider.test", "key", {})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answers": None},
        {"answers": []},
        {"answers": "ok"},
        {"answers": {}},
        [],
        "ok",
        None,
        7,
    ],
)
def test_a_reply_that_is_not_an_envelope_is_an_invalid_response_not_a_crash(monkeypatch, payload):
    provider(monkeypatch, response=payload)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    with pytest.raises(ValueError, match="no action executed"):
        model.choose(page(), "Find a book", [])


def test_an_empty_choices_array_reports_that_nothing_was_typed(monkeypatch):
    """Some providers return no choices at all when a response is filtered."""
    provider(monkeypatch, response={"choices": []})
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test-key")
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a book", "field": {}})


def test_a_reply_missing_the_model_name_is_rejected(monkeypatch):
    """Answers alone are not an envelope: the decision record has to say which model answered."""
    _elements, targets, controls = model.action_space(page()["actions"])
    operations = {*targets, *controls, "DONE", "BLOCKED"}
    provider(monkeypatch, response={"answers": {"operation": choice(operations, "DONE")}})
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    with pytest.raises(ValueError, match="no action executed"):
        model.choose(page(), "Find a book", [])


def start(monkeypatch, fail_at, error=RuntimeError("start failed"), on_close=None):
    """Drive Browser.__init__ to a failure and report the CDP methods it called."""
    import jev_ultrafast.browser as browser

    calls = []

    def cdp(method, **params):
        calls.append(method)
        if method == fail_at:
            raise error
        if method == "Target.closeTarget" and on_close:
            raise on_close
        return {"targetId": "T-1", "sessionId": "S-1"}

    monkeypatch.setattr(browser, "ensure_daemon", lambda: None)
    monkeypatch.setattr(browser, "cdp", cdp)
    return browser.Browser, calls


@pytest.mark.parametrize(
    "fail_at",
    [
        "Target.attachToTarget",
        "Emulation.setDeviceMetricsOverride",
        "Emulation.setFocusEmulationEnabled",
        "Page.navigate",
        "Runtime.evaluate",
    ],
)
def test_a_failed_start_closes_the_tab_it_opened(monkeypatch, fail_at):
    """The tab outlives the error otherwise: no caller ever receives the Browser that owns it."""
    Browser, calls = start(monkeypatch, fail_at)
    with pytest.raises(RuntimeError):
        Browser("https://example.test/")
    assert calls[-1] == "Target.closeTarget"


def test_an_interrupt_during_the_readiness_wait_closes_the_tab(monkeypatch):
    """That wait runs for up to fifteen seconds, which is long enough to be interrupted."""
    Browser, calls = start(monkeypatch, "Runtime.evaluate", error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        Browser("https://example.test/")
    assert calls[-1] == "Target.closeTarget"


def test_a_failed_close_does_not_replace_the_error_that_caused_it(monkeypatch):
    Browser, _ = start(
        monkeypatch, "Page.navigate", error=RuntimeError("bad URL"), on_close=RuntimeError("tab already gone")
    )
    with pytest.raises(RuntimeError, match="bad URL") as caught:
        Browser("https://example.test/")
    assert any("tab already gone" in note for note in caught.value.__notes__)


def test_an_interrupt_while_closing_does_not_replace_the_original_error(monkeypatch):
    Browser, _ = start(
        monkeypatch, "Page.navigate", error=RuntimeError("bad URL"), on_close=KeyboardInterrupt()
    )
    with pytest.raises(RuntimeError, match="bad URL"):
        Browser("https://example.test/")


def test_base_url_falls_back_to_hosted_when_unset_or_blank(monkeypatch):
    """A blank TYPESAFE_BASE_URL must not produce a relative URL like '/systemone'."""
    seen = []

    def post(url, _key, body):
        seen.append(url)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)

    monkeypatch.delenv("TYPESAFE_BASE_URL", raising=False)
    model.choose(page(), "Find a book", [])
    monkeypatch.setenv("TYPESAFE_BASE_URL", "")
    model.choose(page(), "Find a book", [])
    monkeypatch.setenv("TYPESAFE_BASE_URL", "   ")
    model.choose(page(), "Find a book", [])
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://gateway.example.com/typesafe/v1/")
    model.choose(page(), "Find a book", [])

    assert seen == [
        "https://api.typesafe.ai/v1/systemone",
        "https://api.typesafe.ai/v1/systemone",
        # Whitespace is not a usable base URL, but it is truthy, so it is stripped to "" and the
        # trailing-slash strip leaves "/systemone"; guard against that by treating blank as unset.
        "https://api.typesafe.ai/v1/systemone",
        "https://gateway.example.com/typesafe/v1/systemone",
    ]
