import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from window_frame_monitor.agent_control import AgentActionPoller
from window_frame_monitor.models import WindowInfo


class MockExecutor:
    def __init__(self):
        self.focused = []
        self.executed = []

    def focus(self, target):
        self.focused.append(target.hwnd)

    def execute(self, actions):
        self.executed.append(actions)


def test_agent_action_poller_executes_and_acks_pending_action():
    state = {
        "pending": [
            {
                "id": "batch-1",
                "actions": [{"type": "key", "key": "w", "state": "tap", "duration_ms": 10}],
            }
        ],
        "acks": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = state["pending"]
            state["pending"] = []
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            state["acks"].append(json.loads(self.rfile.read(length).decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    executor = MockExecutor()
    poller = AgentActionPoller(
        control_url=f"http://127.0.0.1:{server.server_port}",
        target=WindowInfo(hwnd=123, title="Minecraft"),
        executor=executor,
        poll_ms=50,
    )

    try:
        poller.start()
        for _ in range(20):
            if state["acks"]:
                break
            threading.Event().wait(0.05)
    finally:
        poller.stop()
        server.shutdown()
        thread.join(timeout=2)

    assert executor.focused == [123]
    assert executor.executed == [[{"type": "key", "key": "w", "state": "tap", "duration_ms": 10}]]
    assert state["acks"][0]["id"] == "batch-1"
    assert state["acks"][0]["status"] == "executed"
