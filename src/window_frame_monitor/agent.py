from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from time import perf_counter, perf_counter_ns, sleep
from typing import Any, Callable
from uuid import uuid4


ALLOWED_KEYS = {"w", "a", "s", "d", "space", "e", "shift", "ctrl", *{str(value) for value in range(1, 10)}}
ALLOWED_MOUSE_BUTTONS = {"left", "right"}
MAX_ACTION_DURATION_MS = 1500
MAX_DECISION_DURATION_MS = 3000
MAX_MOUSE_DELTA = 800

MINECRAFT_AGENT_SYSTEM_PROMPT = """
You are a cautious Minecraft visual game agent.
Look at the current game screenshot, compare it with the recent context, infer what changed,
and choose a short safe action sequence toward this goal: break the tree block in front of the player,
or move/turn to find and approach a tree if no tree block is reachable.

Return strict JSON only. Do not use markdown. Do not include explanations outside JSON.
The JSON shape must be:
{
  "observation": {
    "description": "short visual description",
    "change_summary": "what changed since the previous action"
  },
  "plan": {
    "goal": "current high-level goal",
    "subtasks": ["1-3 short subtasks"]
  },
  "steps": ["1-5 short execution steps"],
  "actions": [
    {"type":"key","key":"w","state":"down","duration_ms":600},
    {"type":"mouse","button":"left","state":"down","duration_ms":900},
    {"type":"mouse_move","dx":20,"dy":-5,"duration_ms":150}
  ],
  "confidence": 0.0,
  "requires_human": false
}

Only use these keys: w, a, s, d, space, e, shift, ctrl, 1, 2, 3, 4, 5, 6, 7, 8, 9.
Only use mouse buttons: left, right.
Use short actions. Never exceed 1500 ms for one action or 3000 ms total.
If the scene is uncertain, menu/inventory/chat is open, or the next action may be unsafe, set requires_human true and return no actions.
""".strip()


@dataclass(frozen=True)
class AgentActionBatch:
    id: str
    decision_id: str
    actions: list[dict[str, object]]
    description: str
    created_time_ns: int = field(default_factory=perf_counter_ns)
    status: str = "pending"
    ack: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "decision_id": self.decision_id,
            "actions": self.actions,
            "description": self.description,
            "created_time_ns": self.created_time_ns,
            "status": self.status,
            "ack": self.ack,
        }


class AgentDecisionError(ValueError):
    pass


class AgentActionQueue:
    def __init__(self, *, history_limit: int = 120) -> None:
        self._pending: deque[AgentActionBatch] = deque()
        self._history: deque[dict[str, object]] = deque(maxlen=history_limit)
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def enqueue(self, batch: AgentActionBatch) -> None:
        with self._lock:
            if batch.id in self._seen:
                return
            self._seen.add(batch.id)
            self._pending.append(batch)
            self._history.append({"event": "queued", "batch": batch.to_dict(), "time_ns": perf_counter_ns()})

    def pending(self, *, limit: int = 1) -> list[dict[str, object]]:
        with self._lock:
            return [batch.to_dict() for batch in list(self._pending)[: max(1, limit)]]

    def ack(self, batch_id: str, ack: dict[str, object]) -> dict[str, object]:
        with self._lock:
            for index, batch in enumerate(self._pending):
                if batch.id == batch_id:
                    self._pending.remove(batch)
                    status = str(ack.get("status", "executed"))
                    completed = AgentActionBatch(
                        id=batch.id,
                        decision_id=batch.decision_id,
                        actions=batch.actions,
                        description=batch.description,
                        created_time_ns=batch.created_time_ns,
                        status=status,
                        ack=dict(ack),
                    )
                    result = completed.to_dict()
                    self._history.append({"event": "ack", "batch": result, "time_ns": perf_counter_ns()})
                    return result
            result = {"id": batch_id, "status": "missing", "ack": dict(ack)}
            self._history.append({"event": "ack-missing", "batch": result, "time_ns": perf_counter_ns()})
            return result

    def history(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._history)


class MinecraftAgentOrchestrator:
    def __init__(
        self,
        *,
        latest_jpeg: Callable[[], bytes | None],
        ollama_url: str,
        vlm_model: str,
        context_tokens: int = 40_000,
        max_output_tokens: int = 700,
        tick_interval_s: float = 1.5,
    ) -> None:
        self._latest_jpeg = latest_jpeg
        self._ollama_url = ollama_url.rstrip("/")
        self._vlm_model = vlm_model
        self._context_tokens = max(1024, int(context_tokens))
        self._max_output_tokens = max(1, int(max_output_tokens))
        self._tick_interval_s = max(0.25, float(tick_interval_s))
        self._queue = AgentActionQueue()
        self._history: deque[dict[str, object]] = deque(maxlen=120)
        self._messages: deque[dict[str, object]] = deque(maxlen=12)
        self._lock = threading.Lock()
        self._enabled = True
        self._user_goal = ""
        self._loop_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()

    def start(self) -> None:
        if self._loop_thread and self._loop_thread.is_alive():
            return
        self._loop_thread = threading.Thread(target=self._run_loop, name="minecraft-agent-loop", daemon=True)
        self._loop_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop_thread:
            self._loop_thread.join(timeout=2)

    def state(self) -> dict[str, object]:
        with self._lock:
            latest = self._history[-1] if self._history else None
            return {
                "enabled": self._enabled,
                "tick_interval_s": self._tick_interval_s,
                "user_goal": self._user_goal,
                "pending_count": len(self._queue.pending(limit=100)),
                "latest": latest,
            }

    def set_control(
        self,
        *,
        enabled: bool | None = None,
        tick_interval_s: float | None = None,
        user_goal: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if tick_interval_s is not None:
                self._tick_interval_s = max(0.25, float(tick_interval_s))
            if user_goal is not None:
                self._user_goal = user_goal.strip()[:500]
                self._messages.clear()
        return self.state()

    def tick(self) -> dict[str, object]:
        if not self._tick_lock.acquire(blocking=False):
            return self._record("skipped", {"detail": "agent tick already running"})
        try:
            jpeg = self._latest_jpeg()
            if jpeg is None:
                return self._record("error", {"detail": "No frame available yet."})
            started = perf_counter()
            raw_content = self._call_ollama(jpeg)
            decision = parse_agent_decision(raw_content)
            elapsed_ms = (perf_counter() - started) * 1000
            decision_id = str(uuid4())
            event = {
                "decision_id": decision_id,
                "status": "ok",
                "decision": decision,
                "raw_content": raw_content,
                "elapsed_ms": elapsed_ms,
                "time_ns": perf_counter_ns(),
            }
            if not decision.get("requires_human") and decision.get("actions"):
                self._queue.enqueue(
                    AgentActionBatch(
                        id=str(uuid4()),
                        decision_id=decision_id,
                        actions=list(decision["actions"]),
                        description=str(decision.get("description", "")),
                    )
                )
            with self._lock:
                self._history.append(event)
                self._messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
            return event
        except Exception as exc:
            return self._record("error", {"detail": str(exc)})
        finally:
            self._tick_lock.release()

    def pending_actions(self, *, limit: int = 1) -> list[dict[str, object]]:
        return self._queue.pending(limit=limit)

    def ack_action(self, batch_id: str, ack: dict[str, object]) -> dict[str, object]:
        return self._queue.ack(batch_id, ack)

    def history(self) -> list[dict[str, object]]:
        with self._lock:
            return list(self._history) + self._queue.history()

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                enabled = self._enabled
                interval_s = self._tick_interval_s
            if enabled:
                self.tick()
            self._stop.wait(interval_s)

    def _record(self, status: str, payload: dict[str, object]) -> dict[str, object]:
        event = {"status": status, **payload, "time_ns": perf_counter_ns()}
        with self._lock:
            self._history.append(event)
        return event

    def _call_ollama(self, jpeg: bytes) -> str:
        image_b64 = base64.b64encode(jpeg).decode("ascii")
        messages = self._build_ollama_messages(image_b64)
        payload = {
            "model": self._vlm_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": self._context_tokens,
                "num_predict": self._max_output_tokens,
            },
        }
        request = urllib.request.Request(
            f"{self._ollama_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed: {exc.reason}") from exc
        content = str(body.get("message", {}).get("content", "")).strip()
        if not content:
            raise RuntimeError("Ollama returned an empty agent decision.")
        return content

    def _build_ollama_messages(self, image_b64: str) -> list[dict[str, object]]:
        with self._lock:
            history_messages = list(self._messages)
            user_goal = self._user_goal
        goal_instruction = (
            f"User priority goal: {user_goal}\n"
            "Complete this goal first. After it appears completed, continue with the default Minecraft survival/tree objective."
            if user_goal
            else "No user priority goal is set. Continue with the default Minecraft survival/tree objective."
        )
        return [
            {"role": "system", "content": MINECRAFT_AGENT_SYSTEM_PROMPT},
            *history_messages,
            {
                "role": "user",
                "content": f"{goal_instruction}\nAnalyze this Minecraft frame and return the next JSON decision.",
                "images": [image_b64],
            },
        ]


def parse_agent_decision(content: str) -> dict[str, object]:
    payload = _load_json_from_text(content)
    if not isinstance(payload, dict):
        raise AgentDecisionError("Agent decision must be a JSON object.")
    observation = _require_dict(payload, "observation")
    plan = _require_dict(payload, "plan")
    actions = payload.get("actions", [])
    if not isinstance(actions, list):
        raise AgentDecisionError("actions must be a list.")
    normalized_actions = validate_actions(actions)
    requires_human = bool(payload.get("requires_human", False))
    confidence = float(payload.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))
    description = str(observation.get("description", "")).strip()
    if not description:
        raise AgentDecisionError("observation.description is required.")
    subtasks = plan.get("subtasks", [])
    steps = payload.get("steps", [])
    if not isinstance(subtasks, list) or not isinstance(steps, list):
        raise AgentDecisionError("plan.subtasks and steps must be lists.")
    if requires_human:
        normalized_actions = []
    return {
        "description": description,
        "observation": {
            "description": description,
            "change_summary": str(observation.get("change_summary", "")).strip(),
        },
        "plan": {
            "goal": str(plan.get("goal", "")).strip(),
            "subtasks": [str(item) for item in subtasks[:3]],
        },
        "steps": [str(item) for item in steps[:5]],
        "actions": normalized_actions,
        "confidence": confidence,
        "requires_human": requires_human,
    }


def validate_actions(actions: list[Any]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    total_duration = 0
    for action in actions:
        if not isinstance(action, dict):
            raise AgentDecisionError("Each action must be an object.")
        action_type = str(action.get("type", ""))
        duration_ms = int(action.get("duration_ms", 0))
        if duration_ms < 0 or duration_ms > MAX_ACTION_DURATION_MS:
            raise AgentDecisionError("Action duration is outside the allowed range.")
        total_duration += duration_ms
        if total_duration > MAX_DECISION_DURATION_MS:
            raise AgentDecisionError("Total action duration exceeds the per-decision limit.")
        if action_type == "key":
            key = str(action.get("key", "")).lower()
            if key not in ALLOWED_KEYS:
                raise AgentDecisionError(f"Key is not allowed: {key}")
            normalized.append({"type": "key", "key": key, "state": _normalize_state(action), "duration_ms": duration_ms})
        elif action_type == "mouse":
            button = str(action.get("button", "")).lower()
            if button not in ALLOWED_MOUSE_BUTTONS:
                raise AgentDecisionError(f"Mouse button is not allowed: {button}")
            normalized.append({"type": "mouse", "button": button, "state": _normalize_state(action), "duration_ms": duration_ms})
        elif action_type == "mouse_move":
            dx = int(action.get("dx", 0))
            dy = int(action.get("dy", 0))
            if abs(dx) > MAX_MOUSE_DELTA or abs(dy) > MAX_MOUSE_DELTA:
                raise AgentDecisionError("Mouse movement is outside the allowed range.")
            normalized.append({"type": "mouse_move", "dx": dx, "dy": dy, "duration_ms": duration_ms})
        else:
            raise AgentDecisionError(f"Unsupported action type: {action_type}")
    return normalized


def _normalize_state(action: dict[str, Any]) -> str:
    state = str(action.get("state", "tap")).lower()
    if state not in {"down", "up", "tap"}:
        raise AgentDecisionError(f"Unsupported action state: {state}")
    return state


def _require_dict(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise AgentDecisionError(f"{key} must be an object.")
    return value


def _load_json_from_text(content: str) -> object:
    text = _extract_json_candidate(content)
    for candidate in (text, repair_json_text(text)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise AgentDecisionError("No valid JSON object found in agent response after repair.")


def _extract_json_candidate(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AgentDecisionError("No JSON object found in agent response.")
    return text[start : end + 1]


def repair_json_text(text: str) -> str:
    """Repair common VLM JSON formatting slips without changing field meaning."""
    repaired = text.strip()
    repaired = repaired.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    repaired = repaired.replace("，", ",").replace("：", ":")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = re.sub(
        r'((?:"(?:\\.|[^"\\])*")|(?:\btrue\b|\bfalse\b|\bnull\b)|[}\]]|-?\d+(?:\.\d+)?)\s*\n\s*("[^"\n]+"\s*:)',
        r"\1,\n\2",
        repaired,
    )
    repaired = re.sub(r'([}\]])\s*(?="[^"\n]+"\s*:)', r"\1,\n", repaired)
    repaired = re.sub(r'("[^"\n]+"\s*:)\s*True\b', r"\1 true", repaired)
    repaired = re.sub(r'("[^"\n]+"\s*:)\s*False\b', r"\1 false", repaired)
    repaired = re.sub(r'("[^"\n]+"\s*:)\s*None\b', r"\1 null", repaired)
    return repaired
