import json
from urllib.request import Request, urlopen

from window_frame_monitor.server import create_server


def test_stats_endpoint_returns_runtime_state():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/stats"
        with _serve(server), urlopen(url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert "capture_active" in body
    assert "websocket_clients" in body
    assert "mjpeg_clients" in body


def test_windows_endpoint_returns_list():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/windows"
        with _serve(server), urlopen(url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert isinstance(body, list)
    assert body[0]["title"] == "Test Pattern"


def test_target_endpoint_selects_test_pattern():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/target"
        request = Request(
            url,
            data=json.dumps({"hwnd": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _serve(server), urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert body["target"]["title"] == "Test Pattern"
    assert body["stats"]["active_backend"] == "test-pattern"


def test_stats_endpoint_exposes_pipeline_timing_fields():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/stats"
        with _serve(server), urlopen(url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert "capture_ms" in body
    assert "encode_ms" in body
    assert "serialize_ms" in body
    assert "send_ms" in body


def test_stop_endpoint_stops_capture():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/capture/stop"
        request = Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with _serve(server), urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert body["stats"]["capture_active"] is False


def test_refresh_endpoint_returns_stats():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/capture/refresh"
        request = Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with _serve(server), urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert "stats" in body


def test_settings_endpoint_updates_target_fps():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/settings"
        request = Request(
            url,
            data=json.dumps({"target_fps": 24}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _serve(server), urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert body["stats"]["target_fps"] == 24


def test_settings_endpoint_updates_h264_options():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/settings"
        request = Request(
            url,
            data=json.dumps(
                {
                    "h264_encoder": "libx264",
                    "h264_bitrate_kbps": 2400,
                    "output_width": 854,
                    "output_height": 480,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _serve(server), urlopen(request, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert body["stats"]["h264_encoder"] == "libx264"
    assert body["stats"]["h264_bitrate_kbps"] == 2400
    assert body["stats"]["output_width"] == 854
    assert body["stats"]["output_height"] == 480


def test_settings_endpoint_can_match_h264_output_to_source_size():
    server = create_server(port=0, use_test_backend=True)
    try:
        target_url = f"http://127.0.0.1:{server.server_port}/api/target"
        settings_url = f"http://127.0.0.1:{server.server_port}/api/settings"
        target_request = Request(
            target_url,
            data=json.dumps({"hwnd": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        settings_request = Request(
            settings_url,
            data=json.dumps({"match_source_output": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _serve(server):
            urlopen(target_request, timeout=3).read()
            with urlopen(settings_request, timeout=3) as response:
                body = json.loads(response.read().decode("utf-8"))
    finally:
        server.server_close()

    assert body["stats"]["output_width"] == 640
    assert body["stats"]["output_height"] == 360


def test_h264_test_endpoint_requires_selected_target():
    server = create_server(port=0, use_test_backend=True)
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/h264-test/start"
        request = Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        with _serve(server):
            try:
                urlopen(request, timeout=3)
            except Exception as exc:
                response = exc
    finally:
        server.server_close()

    assert getattr(response, "code", None) == 400


class _serve:
    def __init__(self, server):
        self.server = server
        self.thread = None

    def __enter__(self):
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=3)
