"""Offline transport retries for model HTTP calls. No paid APIs."""

from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import model


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("temporary"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ],
)
def test_transient_connection_errors_are_retried(monkeypatch, error):
    success = Mock(status_code=200, is_error=False)
    success.json.return_value = {"ok": True}
    post = Mock(side_effect=[error, success])
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    assert model.post_json("https://example.test/v1", "k", {}) == {"ok": True}
    assert post.call_count == 2
    assert [c.args for c in sleep.call_args_list] == [(0.5,)]


def test_exhausted_connection_errors_still_fail(monkeypatch):
    post = Mock(side_effect=httpx.ConnectError("down"))
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="Model connection failed"):
        model.post_json("https://example.test/v1", "k", {})
    assert post.call_count == 3
    assert [c.args for c in sleep.call_args_list] == [(0.5,), (1.0,)]


@pytest.mark.parametrize(
    "error",
    [httpx.UnsupportedProtocol("ftp"), httpx.TooManyRedirects("loop"), httpx.DecodingError("body")],
)
def test_permanent_errors_are_not_retried(monkeypatch, error):
    post = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="Model connection failed"):
        model.post_json("https://example.test/v1", "k", {})
    assert post.call_count == 1
    sleep.assert_not_called()


def test_provider_errors_carry_the_provider_detail(monkeypatch):
    response = Mock(status_code=400, is_error=True, text='{"error":\n  {"type":"MissingSessionID"}}')
    monkeypatch.setattr(model.CLIENT, "post", Mock(return_value=response))
    with pytest.raises(RuntimeError, match='HTTP 400; no action executed. {"error": {"type":"MissingSessionID"}}'):
        model.post_json("https://example.test/v1", "k", {})


def respond(monkeypatch, *responses):
    post = Mock(side_effect=list(responses))
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    return post, sleep


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        ({"message": "Model\n  overloaded", "code": 500}, "Model overloaded"),
        ("rate_limited", "rate_limited"),
        ({"type": "invalid_request_error"}, '{"type": "invalid_request_error"}'),
    ],
)
def test_a_2xx_error_body_is_a_provider_error(monkeypatch, error, detail):
    post, sleep = respond(monkeypatch, httpx.Response(200, json={"id": "gen-1", "error": error}))
    with pytest.raises(RuntimeError) as raised:
        model.post_json("https://example.test/v1", "k", {})
    assert str(raised.value) == f"Model provider returned an error; no action executed. {detail}"
    assert post.call_count == 1
    sleep.assert_not_called()


def test_a_long_error_body_message_is_trimmed(monkeypatch):
    respond(monkeypatch, httpx.Response(200, json={"error": {"message": "x" * 1000}}))
    with pytest.raises(RuntimeError) as raised:
        model.post_json("https://example.test/v1", "k", {})
    assert str(raised.value).endswith("no action executed. " + "x" * 300)


@pytest.mark.parametrize(
    "error",
    [
        {"message": "Upstream idle timeout exceeded", "code": 504},
        {"message": "Rate limited", "code": "429"},
        {"type": "overloaded_error", "message": "Overloaded"},
    ],
)
def test_a_transient_error_body_is_retried(monkeypatch, error):
    post, sleep = respond(
        monkeypatch, httpx.Response(200, json={"error": error}), httpx.Response(200, json={"choices": []})
    )
    assert model.post_json("https://example.test/v1", "k", {}) == {"choices": []}
    assert post.call_count == 2
    assert [c.args for c in sleep.call_args_list] == [(0.5,)]


def test_a_persistent_transient_error_body_still_fails(monkeypatch):
    busy = httpx.Response(200, json={"error": {"type": "overloaded_error", "message": "Overloaded"}})
    post, _ = respond(monkeypatch, busy, busy, busy)
    with pytest.raises(RuntimeError, match="returned an error; no action executed. Overloaded"):
        model.post_json("https://example.test/v1", "k", {})
    assert post.call_count == 3


def test_http_504_is_retried(monkeypatch):
    post, _ = respond(monkeypatch, httpx.Response(504), httpx.Response(200, json={"choices": []}))
    assert model.post_json("https://example.test/v1", "k", {}) == {"choices": []}
    assert post.call_count == 2


@pytest.mark.parametrize("body", [{"choices": [], "error": None}, ["not", "an", "envelope"]])
def test_a_success_body_is_returned_unchanged(monkeypatch, body):
    respond(monkeypatch, httpx.Response(200, json=body))
    assert model.post_json("https://example.test/v1", "k", {}) == body


def test_a_non_json_2xx_body_is_not_retried(monkeypatch):
    post, sleep = respond(monkeypatch, httpx.Response(200, content=b"<html>Bad Gateway</html>"))
    with pytest.raises(RuntimeError, match="non-JSON response; no action executed"):
        model.post_json("https://example.test/v1", "k", {})
    assert post.call_count == 1
    sleep.assert_not_called()
