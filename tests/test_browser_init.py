"""Offline Browser constructor races. No Chrome process or paid APIs."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser


def test_destroyed_context_during_navigate_does_not_abort_init(monkeypatch):
    calls = []

    def cdp(method, **_params):
        calls.append(method)
        if method == "Target.createTarget":
            return {"targetId": "owned-tab"}
        if method == "Target.attachToTarget":
            return {"sessionId": "owned-session"}
        if method == "Runtime.evaluate":
            if calls.count("Runtime.evaluate") == 1:
                return {"exceptionDetails": {"text": "Inspected target navigated or closed"}}
            return {"result": {"value": "complete"}}
        return {}

    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", Mock())
    owned = browser.Browser("https://example.test/")
    assert owned.target == "owned-tab"
    assert owned.session == "owned-session"
    assert calls.count("Runtime.evaluate") >= 2
    assert calls.count("Page.navigate") == 1


def test_context_that_stays_destroyed_is_polled_until_the_deadline(monkeypatch):
    calls = []

    def cdp(method, **_params):
        calls.append(method)
        if method == "Target.createTarget":
            return {"targetId": "owned-tab"}
        if method == "Target.attachToTarget":
            return {"sessionId": "owned-session"}
        if method == "Runtime.evaluate":
            return {"exceptionDetails": {"text": "Inspected target navigated or closed"}}
        return {}

    # Each clock reading advances one second, so the 15 s deadline passes after a bounded number of polls.
    clock = iter(range(1000))
    monkeypatch.setattr(browser, "ensure_daemon", Mock())
    monkeypatch.setattr(browser, "cdp", cdp)
    monkeypatch.setattr(browser.time, "sleep", Mock())
    monkeypatch.setattr(browser.time, "monotonic", lambda: next(clock))
    owned = browser.Browser("https://example.test/")
    assert owned.session == "owned-session"
    assert calls.count("Runtime.evaluate") == 14
    assert calls.count("Page.navigate") == 1


@pytest.mark.parametrize(("raw", "expected"), [(None, (1470, 1470)), ("", (1470, 1470)), ("1470x830", (1470, 830)),
                                               (" 1470X1660 ", (1470, 1660))])
def test_viewport_comes_from_the_environment(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("JEV_VIEWPORT", raising=False)
    else:
        monkeypatch.setenv("JEV_VIEWPORT", raw)
    assert browser.viewport() == expected


@pytest.mark.parametrize("raw", ["1470", "wide", "1470x830x2", "100x830", "1470x9000"])
def test_invalid_viewports_are_rejected(monkeypatch, raw):
    monkeypatch.setenv("JEV_VIEWPORT", raw)
    with pytest.raises(ValueError, match="JEV_VIEWPORT"):
        browser.viewport()
