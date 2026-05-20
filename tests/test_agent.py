import json

from PIL import Image

from window_frame_monitor.agent import AgentActionBatch, AgentActionQueue, AgentDecisionError, MinecraftAgentOrchestrator, parse_agent_decision


def _decision(*, requires_human=False, actions=None):
    return {
        "observation": {"description": "A tree is in front of the player.", "change_summary": "The view moved closer."},
        "plan": {"goal": "Break the tree block.", "subtasks": ["aim at trunk", "hold attack"]},
        "steps": ["face the trunk", "hold left mouse"],
        "actions": actions if actions is not None else [{"type": "mouse", "button": "left", "state": "down", "duration_ms": 900}],
        "confidence": 0.8,
        "requires_human": requires_human,
    }


def test_parse_agent_decision_accepts_json_object():
    parsed = parse_agent_decision(json.dumps(_decision()))

    assert parsed["observation"]["description"].startswith("A tree")
    assert parsed["actions"][0]["button"] == "left"
    assert parsed["confidence"] == 0.8


def test_parse_agent_decision_extracts_markdown_wrapped_json():
    parsed = parse_agent_decision("```json\n" + json.dumps(_decision()) + "\n```")

    assert parsed["plan"]["goal"] == "Break the tree block."


def test_parse_agent_decision_extracts_json_from_extra_text():
    parsed = parse_agent_decision("Here is the action:\n" + json.dumps(_decision()) + "\nDone.")

    assert parsed["steps"] == ["face the trunk", "hold left mouse"]


def test_parse_agent_decision_rejects_illegal_key():
    payload = _decision(actions=[{"type": "key", "key": "escape", "state": "tap", "duration_ms": 50}])

    try:
        parse_agent_decision(json.dumps(payload))
    except AgentDecisionError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("Expected illegal key to be rejected.")


def test_parse_agent_decision_rejects_long_action():
    payload = _decision(actions=[{"type": "key", "key": "w", "state": "down", "duration_ms": 2000}])

    try:
        parse_agent_decision(json.dumps(payload))
    except AgentDecisionError as exc:
        assert "duration" in str(exc)
    else:
        raise AssertionError("Expected long action to be rejected.")


def test_requires_human_clears_actions():
    parsed = parse_agent_decision(json.dumps(_decision(requires_human=True)))

    assert parsed["requires_human"] is True
    assert parsed["actions"] == []


def test_action_queue_dedupes_and_acks():
    queue = AgentActionQueue()
    batch = AgentActionBatch(id="batch-1", decision_id="decision-1", actions=[], description="x")

    queue.enqueue(batch)
    queue.enqueue(batch)
    pending = queue.pending(limit=10)
    ack = queue.ack("batch-1", {"status": "executed"})

    assert len(pending) == 1
    assert ack["status"] == "executed"
    assert queue.pending(limit=10) == []


def test_agent_tick_reports_error_without_frame():
    agent = MinecraftAgentOrchestrator(latest_jpeg=lambda: None, ollama_url="http://127.0.0.1:1", vlm_model="mock")

    result = agent.tick()

    assert result["status"] == "error"
    assert "No frame" in result["detail"]


def test_agent_tick_does_not_enqueue_requires_human(monkeypatch):
    image = Image.new("RGB", (8, 8), "black")
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    agent = MinecraftAgentOrchestrator(latest_jpeg=buffer.getvalue, ollama_url="http://127.0.0.1:1", vlm_model="mock")
    monkeypatch.setattr(agent, "_call_ollama", lambda _jpeg: json.dumps(_decision(requires_human=True)))

    result = agent.tick()

    assert result["status"] == "ok"
    assert agent.pending_actions(limit=10) == []


def test_agent_tick_enqueues_valid_actions(monkeypatch):
    image = Image.new("RGB", (8, 8), "black")
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    agent = MinecraftAgentOrchestrator(latest_jpeg=buffer.getvalue, ollama_url="http://127.0.0.1:1", vlm_model="mock")
    monkeypatch.setattr(agent, "_call_ollama", lambda _jpeg: json.dumps(_decision()))

    result = agent.tick()
    pending = agent.pending_actions(limit=10)

    assert result["status"] == "ok"
    assert len(pending) == 1
    assert pending[0]["actions"][0]["type"] == "mouse"
