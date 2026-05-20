from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from time import perf_counter, sleep
from typing import Protocol

from window_frame_monitor.models import WindowInfo


class InputExecutor(Protocol):
    def focus(self, target: WindowInfo) -> None: ...

    def execute(self, actions: list[dict[str, object]]) -> None: ...


class WindowsInputExecutor:
    def focus(self, target: WindowInfo) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Input execution is only supported on Windows.")
        if target.hwnd:
            import win32gui

            if not win32gui.IsWindow(target.hwnd):
                raise RuntimeError("Target window no longer exists.")
            if win32gui.IsIconic(target.hwnd):
                raise RuntimeError("Target window is minimized.")
            win32gui.SetForegroundWindow(target.hwnd)
            sleep(0.05)

    def execute(self, actions: list[dict[str, object]]) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Input execution is only supported on Windows.")
        for action in actions:
            action_type = str(action.get("type", ""))
            duration_s = max(0.0, int(action.get("duration_ms", 0)) / 1000)
            if action_type == "key":
                key = str(action.get("key", "")).lower()
                state = str(action.get("state", "tap")).lower()
                _send_key(key, state, duration_s)
            elif action_type == "mouse":
                button = str(action.get("button", "")).lower()
                state = str(action.get("state", "tap")).lower()
                _send_mouse_button(button, state, duration_s)
            elif action_type == "mouse_move":
                _send_mouse_move(int(action.get("dx", 0)), int(action.get("dy", 0)))
                if duration_s:
                    sleep(duration_s)
            else:
                raise RuntimeError(f"Unsupported action type: {action_type}")


class AgentActionPoller:
    def __init__(
        self,
        *,
        control_url: str,
        target: WindowInfo,
        executor: InputExecutor | None = None,
        poll_ms: int = 250,
    ) -> None:
        self._control_url = control_url.rstrip("/")
        self._target = target
        self._executor = executor or WindowsInputExecutor()
        self._poll_s = max(0.05, poll_ms / 1000)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="agent-action-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for batch in self._pending():
                    self._execute_batch(batch)
            except Exception:
                pass
            self._stop.wait(self._poll_s)

    def _pending(self) -> list[dict[str, object]]:
        request = urllib.request.Request(f"{self._control_url}/agent/actions/pending?limit=1", method="GET")
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        return body if isinstance(body, list) else []

    def _execute_batch(self, batch: dict[str, object]) -> None:
        batch_id = str(batch.get("id", ""))
        actions = batch.get("actions", [])
        if not batch_id or not isinstance(actions, list):
            return
        started = perf_counter()
        try:
            self._executor.focus(self._target)
            self._executor.execute([action for action in actions if isinstance(action, dict)])
            self._ack(batch_id, {"status": "executed", "elapsed_ms": (perf_counter() - started) * 1000})
        except Exception as exc:
            self._ack(batch_id, {"status": "error", "error": str(exc), "elapsed_ms": (perf_counter() - started) * 1000})

    def _ack(self, batch_id: str, ack: dict[str, object]) -> None:
        request = urllib.request.Request(
            f"{self._control_url}/agent/actions/ack",
            data=json.dumps({"id": batch_id, **ack}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                pass
        except urllib.error.URLError:
            pass


def _send_key(key: str, state: str, duration_s: float) -> None:
    import win32api
    import win32con

    vk = _key_to_vk(key, win32con)
    if state in {"down", "tap"}:
        win32api.keybd_event(vk, 0, 0, 0)
    if duration_s:
        sleep(duration_s)
    if state in {"up", "tap", "down"}:
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def _send_mouse_button(button: str, state: str, duration_s: float) -> None:
    import win32api
    import win32con

    down_flag, up_flag = (
        (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP)
        if button == "left"
        else (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP)
    )
    if state in {"down", "tap"}:
        win32api.mouse_event(down_flag, 0, 0, 0, 0)
    if duration_s:
        sleep(duration_s)
    if state in {"up", "tap", "down"}:
        win32api.mouse_event(up_flag, 0, 0, 0, 0)


def _send_mouse_move(dx: int, dy: int) -> None:
    import win32api
    import win32con

    win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, dx, dy, 0, 0)


def _key_to_vk(key: str, win32con: object) -> int:
    special = {
        "space": getattr(win32con, "VK_SPACE"),
        "shift": getattr(win32con, "VK_SHIFT"),
        "ctrl": getattr(win32con, "VK_CONTROL"),
    }
    if key in special:
        return int(special[key])
    if len(key) == 1:
        return ord(key.upper())
    raise RuntimeError(f"Unsupported key: {key}")
