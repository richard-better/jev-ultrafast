"""Offline contracts for URL maps. No browsers or model calls."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast import url_map as maps
from jev_ultrafast.browser import StalePage
from jev_ultrafast.model import action_space

ENTRY = {
    "id": "shop_search",
    "description": "Search results for a product query",
    "url": "https://shop.example/search?q={query}&from={day}&size={size}&n={count}",
    "match": {"path_prefix": "/"},
    "parameters": {
        "query": {"type": "text", "description": "What to search for", "max_length": 40},
        "day": {"type": "date", "description": "Earliest delivery day"},
        "size": {"type": "choice", "description": "Size", "options": ["small", "large"]},
        "count": {"type": "integer", "description": "How many", "min": 1, "max": 5},
    },
}
VALUES = {"query": "desk lamp", "day": "2026-10-29", "size": "small", "count": 2}


def entry(**changes):
    return maps.load({"entries": [{**ENTRY, **changes}]})[0]


def test_the_google_flights_example_map_loads():
    entries = maps.load("examples/maps/google-flights.json")
    assert [e["operation"] for e in entries] == ["OPEN_GOOGLE_FLIGHTS_ONE_WAY", "OPEN_GOOGLE_FLIGHTS_ROUND_TRIP"]


def test_maps_come_from_the_environment(tmp_path, monkeypatch):
    path = tmp_path / "shop.json"
    path.write_text(json.dumps({"entries": [ENTRY]}))
    monkeypatch.setenv("JEV_URL_MAP", str(path))
    assert [e["id"] for e in maps.load()] == ["shop_search"]
    monkeypatch.delenv("JEV_URL_MAP")
    assert maps.load() == []


@pytest.mark.parametrize("changes", [
    {"id": "Shop-Search"},
    {"description": "short"},
    {"url": "http://shop.example/search?q={query}&from={day}&size={size}&n={count}"},
    {"url": "https://{query}.example/?from={day}&size={size}&n={count}"},
    {"url": "https://shop.example/search?q={query}"},
    {"parameters": {**ENTRY["parameters"], "size": {"type": "choice", "description": "Size"}}},
    {"parameters": {**ENTRY["parameters"], "count": {"type": "number", "description": "How many"}}},
])
def test_invalid_entries_fail_when_the_map_loads(changes):
    with pytest.raises(maps.UrlMapError):
        entry(**changes)


def test_duplicate_entry_ids_are_rejected():
    with pytest.raises(maps.UrlMapError, match="unique"):
        maps.load({"entries": [ENTRY, ENTRY]})


def test_entries_are_offered_only_on_their_own_site_and_path():
    shop = entry(match={"path_prefix": "/search"})
    assert [a["id"] for a in maps.actions([shop], "https://shop.example/search")] == ["open_shop_search"]
    assert maps.actions([shop], "https://shop.example/account") == []
    assert maps.actions([shop], "https://evil.example/search") == []
    assert maps.actions([shop], "http://shop.example/search") == []


def test_parameters_are_validated_and_encoded_into_the_entry_url():
    url = maps.build(entry(), {**VALUES, "query": "Zürich & more/#x?y=1"})
    assert url == ("https://shop.example/search?q=Z%C3%BCrich%20%26%20more%2F%23x%3Fy%3D1"
                   "&from=2026-10-29&size=small&n=2")


@pytest.mark.parametrize("changes", [
    {"query": ""}, {"query": "x" * 41}, {"query": "line\nbreak"}, {"query": 7},
    {"day": "29.10.2026"}, {"day": "2026-02-30"}, {"day": "2026-10-29T00:00"},
    {"size": "Small"}, {"size": "medium"},
    {"count": 0}, {"count": 6}, {"count": "2"}, {"count": True},
])
def test_invalid_values_never_become_a_url(changes):
    with pytest.raises(maps.UrlMapError):
        maps.build(entry(), {**VALUES, **changes})


@pytest.mark.parametrize("values", [{k: v for k, v in VALUES.items() if k != "size"}, {**VALUES, "url": "x"}, None])
def test_parameters_must_be_exactly_the_declared_ones(values):
    with pytest.raises(maps.UrlMapError):
        maps.build(entry(), values)


def test_an_entry_is_its_own_operation():
    _, targets, controls = action_space([*maps.actions([entry()], "https://shop.example/")])
    assert not targets
    assert controls["OPEN_SHOP_SEARCH"]["kind"] == "shortcut"


@pytest.mark.parametrize(("content", "accepted"), [
    (json.dumps({"parameters": VALUES}), True),
    ("```json\n" + json.dumps({"parameters": VALUES}) + "\n```", True),
    ("```\n" + json.dumps({"parameters": None}) + "\n```", False),
    (json.dumps({"parameters": None}), False),
    (json.dumps({"parameters": VALUES, "url": "https://x"}), False),
    ("https://shop.example/search?q=lamp", False),
])
def test_the_text_model_returns_only_parameters(monkeypatch, content, accepted):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    if accepted:
        assert model.shortcut_parameters({"goal": "x"}, "run")[0] == VALUES
    else:
        with pytest.raises(ValueError, match="nothing opened"):
            model.shortcut_parameters({"goal": "x"}, "run")


@pytest.fixture
def shop_runner():
    page = {
        "url": "https://shop.example/", "title": "Shop", "text": "Shop", "fingerprint": "home",
        "actions": [*maps.actions([entry()], "https://shop.example/")],
    }
    browser = Mock(fresh=Mock(return_value=True), observe=Mock(return_value={**page, "fingerprint": "results"}))
    browser.act.return_value = {"executed": "open_shop_search"}
    agent = loop.Agent.__new__(loop.Agent)
    agent.screenshots, agent.max_steps, agent.pending_text, agent.session = False, 60, None, "run"
    agent.shortcuts = [entry()]
    agent.state = {
        "browser": browser, "page": page, "goal": "Find a small desk lamp delivered from October 29, 2026, two of them",
        "history": [], "decisions": [], "status": "predicted", "started_at": 0.0, "record": False, "text_calls": [],
        "decision": {"choice": "open_shop_search", "operation": "OPEN_SHOP_SEARCH", "target": None, "confidence": 0.9,
                     "probabilities": {"open_shop_search": 0.9}, "latency_ms": 10, "usage": {}},
    }
    return agent


def test_a_chosen_shortcut_opens_the_validated_url(shop_runner, monkeypatch):
    monkeypatch.setattr(loop, "shortcut_parameters", Mock(return_value=(VALUES, {"model": "t", "latency_ms": 5})))
    shop_runner.command("act", {"fingerprint": "home"})
    acted = shop_runner.state["browser"].act.call_args.args[0]
    expected = "https://shop.example/search?q=desk%20lamp&from=2026-10-29&size=small&n=2"
    assert acted["url"] == expected and acted["navigation"] == {"url": expected, "target": "_self"}
    entry_ = shop_runner.state["history"][-1]
    assert (entry_["kind"], entry_["text"], entry_["execution"], entry_["page_changed"]) == (
        "shortcut", expected, "confirmed", True)
    assert shop_runner.state["text_calls"][-1]["parameters"] == VALUES


def test_a_stale_shortcut_retry_reuses_the_built_url_only_for_the_same_entry(shop_runner, monkeypatch):
    fill = Mock(return_value=("lamp", {"model": "t", "latency_ms": 5}))
    parameters = Mock(return_value=(VALUES, {"model": "t", "latency_ms": 5}))
    monkeypatch.setattr(loop, "field_text", fill)
    monkeypatch.setattr(loop, "shortcut_parameters", parameters)
    browser, decision = shop_runner.state["browser"], shop_runner.state["decision"]
    browser.act.side_effect = [StalePage("Changed before input"), {"executed": "open_shop_search"}]
    with pytest.raises(StalePage):
        shop_runner.command("act", {"fingerprint": "home"})
    shop_runner.state["decision"] = decision
    shop_runner.command("act", {"fingerprint": "home"})
    assert parameters.call_count == 1
    assert browser.act.call_args_list[0].kwargs == browser.act.call_args_list[1].kwargs
    assert shop_runner.pending_text is None
    fill.assert_not_called()


def test_invalid_shortcut_parameters_open_nothing_and_are_logged(shop_runner, monkeypatch):
    bad = {**VALUES, "size": "medium"}
    monkeypatch.setattr(loop, "shortcut_parameters", Mock(return_value=(bad, {"model": "t", "latency_ms": 5})))
    with pytest.raises(maps.UrlMapError):
        shop_runner.command("act", {"fingerprint": "home"})
    shop_runner.state["browser"].act.assert_not_called()
    assert not shop_runner.state["history"]
    assert shop_runner.state["text_calls"][-1] == {
        "model": "t", "latency_ms": 5, "field": "Open directly: Search results for a product query",
        "parameters": bad, "value": None,
    }


def test_reopening_the_same_shortcut_is_not_progress():
    opened = {"page_changed": True, "kind": "shortcut", "action": "Open directly: search", "text": "https://x/?q=a"}
    assert loop.stalled([opened, opened, opened])
    assert not loop.stalled([opened, opened, {**opened, "text": "https://x/?q=b"}])
