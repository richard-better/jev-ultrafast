"""Recording lifecycle contracts; no browser, model service or external site."""

import base64
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_flights.py"


def load_script():
    spec = importlib.util.spec_from_file_location("record_flights", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recording(monkeypatch, tmp_path):
    script = load_script()
    state = {"page": {}, "history": [{"action": "Search"}], "decisions": [], "elapsed_ms": 10, "status": "done"}
    agent = Mock()
    agent.browser.call.return_value = {"data": base64.b64encode(b"jpeg").decode()}
    agent.browser.observe.return_value = {"url": "https://example.test/", "actions": []}
    agent.browser.target, agent.browser.session = "target", "session"
    agent.run.return_value = iter([state])
    agent.snapshot.side_effect = lambda: dict(state)
    verification = {"passed": True}
    monkeypatch.setattr(script, "Agent", Mock(return_value=agent))
    monkeypatch.setattr(script, "viewport", lambda: (1120, 780))
    monkeypatch.setattr(script, "drain_events", lambda: [])
    monkeypatch.setattr(script, "task", lambda _name: ("goal", "https://example.test/", lambda *_: verification))
    return script, agent, tmp_path / "run", verification


def test_a_successful_recording_closes_the_agent(recording):
    script, agent, folder, _ = recording
    script.main([str(folder)])
    agent.close.assert_called_once_with()
    assert json.loads((folder / "state.json").read_text())["verification"] == {"passed": True}


def test_a_failed_run_still_saves_its_state_and_closes_the_agent(recording):
    script, agent, folder, _ = recording
    agent.run.side_effect = RuntimeError("execution interrupted")
    with pytest.raises(RuntimeError, match="execution interrupted"):
        script.main([str(folder)])
    agent.close.assert_called_once_with()
    assert (folder / "state.json").exists()


def test_a_failure_before_the_run_closes_the_agent(recording):
    script, agent, folder, _ = recording
    agent.browser.call.side_effect = TimeoutError("screenshot timed out")
    with pytest.raises(TimeoutError):
        script.main([str(folder)])
    agent.run.assert_not_called()
    agent.close.assert_called_once_with()


def test_a_failed_verification_closes_the_agent_before_exiting(recording):
    script, agent, folder, verification = recording
    verification["passed"] = False
    with pytest.raises(SystemExit, match="verification failed"):
        script.main([str(folder)])
    agent.close.assert_called_once_with()


def test_keep_open_leaves_an_interrupted_run_inspectable(recording):
    script, agent, folder, _ = recording
    agent.run.side_effect = RuntimeError("execution interrupted")
    with pytest.raises(RuntimeError):
        script.main([str(folder), "--keep-open"])
    agent.close.assert_not_called()
