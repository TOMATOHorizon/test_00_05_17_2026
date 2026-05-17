from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

from window_frame_monitor.backends.dxgi import DxgiDesktopDuplicationBackend
from window_frame_monitor.backends.nvidia import NvidiaNvFbcCaptureBackend
from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.backends.windows import WindowsWindowCaptureBackend
from window_frame_monitor.frame_hub import FrameHub
from window_frame_monitor.h264_probe import run_h264_probe
from window_frame_monitor.models import WindowInfo
from window_frame_monitor.windows import list_windows, resolve_window


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class MonitorRuntime:
    def __init__(self, use_test_backend: bool = False, target_fps: int = 30) -> None:
        if use_test_backend:
            backends = [TestPatternBackend()]
        else:
            backends = [
                NvidiaNvFbcCaptureBackend(),
                DxgiDesktopDuplicationBackend(),
                WindowsWindowCaptureBackend(),
                TestPatternBackend(),
            ]
        self.use_test_backend = use_test_backend
        self.hub = FrameHub(backends=backends, target_fps=target_fps)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="frame-hub-loop", daemon=True)
        self._thread.start()

    def close(self) -> None:
        future = asyncio.run_coroutine_threadsafe(self.hub.close(), self.loop)
        future.result(timeout=3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=3)

    def list_windows(self) -> list[WindowInfo]:
        if self.use_test_backend:
            return [WindowInfo(hwnd=0, title="Test Pattern", process_name="test-pattern", width=640, height=360)]
        return list_windows()

    def set_target(self, *, hwnd: int | None, title: str | None, process_name: str | None) -> dict[str, Any]:
        if self.use_test_backend:
            window = WindowInfo(hwnd=0, title="Test Pattern", process_name="test-pattern", width=640, height=360)
        else:
            window = resolve_window(hwnd=hwnd, title=title, process_name=process_name)
        self.run(self.hub.set_target(window))
        return {"target": asdict(window), "stats": self.hub.stats_dict()}

    def stop_capture(self) -> dict[str, Any]:
        self.run(self.hub.stop_capture(clear_clients=True))
        return {"stats": self.hub.stats_dict()}

    def refresh_capture(self) -> dict[str, Any]:
        self.run(self.hub.refresh_capture())
        return {"stats": self.hub.stats_dict()}

    def update_settings(
        self,
        *,
        target_fps: int | None = None,
        h264_encoder: str | None = None,
        h264_bitrate_kbps: int | None = None,
        output_width: int | None = None,
        output_height: int | None = None,
        match_source_output: bool = False,
    ) -> dict[str, Any]:
        if target_fps is not None:
            self.run(self.hub.set_target_fps(target_fps))
        if match_source_output:
            self.run(self.hub.match_h264_output_to_source())
        if any(value is not None for value in (h264_encoder, h264_bitrate_kbps, output_width, output_height)):
            self.run(
                self.hub.set_h264_settings(
                    encoder=h264_encoder,
                    bitrate_kbps=h264_bitrate_kbps,
                    width=output_width,
                    height=output_height,
                )
            )
        return {"stats": self.hub.stats_dict()}

    def run_h264_test(self, *, duration_s: float = 3.0) -> dict[str, Any]:
        if not self.hub.has_target:
            raise RuntimeError("Select a target window before running the H.264 test.")
        duration_s = max(0.5, min(10.0, float(duration_s)))
        result = self.run(run_h264_probe(self.hub, duration_s=duration_s), timeout=duration_s + 12)
        return {"probe": asdict(result), "stats": self.hub.stats_dict()}

    def run(self, coro: Any, *, timeout: float = 5) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=timeout)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()


class MonitorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], runtime: MonitorRuntime) -> None:
        self.runtime = runtime
        super().__init__(server_address, MonitorRequestHandler)

    def server_close(self) -> None:
        try:
            self.runtime.close()
        finally:
            super().server_close()


class MonitorRequestHandler(BaseHTTPRequestHandler):
    server: MonitorHTTPServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_static("monitor.html", "text/html; charset=utf-8")
        elif path == "/static/monitor.js":
            self._send_static("monitor.js", "text/javascript; charset=utf-8")
        elif path == "/static/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
        elif path == "/api/windows":
            self._send_json([asdict(window) for window in self.server.runtime.list_windows()])
        elif path == "/api/stats":
            self._send_json(self.server.runtime.hub.stats_dict())
        elif path == "/stream.mjpg":
            self._stream_mjpeg()
        elif path == "/ws/frames":
            self._stream_websocket()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/target":
            self._handle_target_post()
        elif path == "/api/capture/stop":
            self._send_json(self.server.runtime.stop_capture())
        elif path == "/api/capture/refresh":
            self._send_json(self.server.runtime.refresh_capture())
        elif path == "/api/settings":
            self._handle_settings_post()
        elif path == "/api/h264-test/start":
            self._handle_h264_test_post()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

    def _handle_target_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = self.server.runtime.set_target(
                hwnd=body.get("hwnd"),
                title=body.get("title"),
                process_name=body.get("process_name"),
            )
        except Exception as exc:
            self._send_json({"detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(result)

    def _handle_h264_test_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = self.server.runtime.run_h264_test(duration_s=float(body.get("duration_s", 3.0)))
        except Exception as exc:
            self._send_json({"detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(result)

    def _handle_settings_post(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            target_fps = body.get("target_fps")
            if target_fps is not None:
                target_fps = int(target_fps)
            result = self.server.runtime.update_settings(
                target_fps=target_fps,
                h264_encoder=body.get("h264_encoder"),
                h264_bitrate_kbps=int(body["h264_bitrate_kbps"]) if body.get("h264_bitrate_kbps") is not None else None,
                output_width=int(body["output_width"]) if body.get("output_width") is not None else None,
                output_height=int(body["output_height"]) if body.get("output_height") is not None else None,
                match_source_output=bool(body.get("match_source_output")),
            )
        except Exception as exc:
            self._send_json({"detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._send_json(result)

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def _send_static(self, name: str, content_type: str) -> None:
        static_dir = files("window_frame_monitor").joinpath("static")
        data = static_dir.joinpath(name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream_mjpeg(self) -> None:
        hub = self.server.runtime.hub
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.server.runtime.run(hub.add_mjpeg_client())
        last_frame_id = 0
        try:
            while True:
                result = self.server.runtime.run(hub.wait_for_next_jpeg(after_frame_id=last_frame_id))
                if result is None:
                    continue
                last_frame_id, jpeg = result
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            return
        finally:
            self.server.runtime.run(hub.remove_mjpeg_client())

    def _stream_websocket(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(HTTPStatus.BAD_REQUEST, "Missing Sec-WebSocket-Key")
            return

        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        hub = self.server.runtime.hub
        self.server.runtime.run(hub.add_websocket_client())
        last_frame_id = 0
        try:
            while True:
                result = self.server.runtime.run(hub.wait_for_next_jpeg(after_frame_id=last_frame_id))
                if result is None:
                    _send_ws_text(self.connection, json.dumps({"type": "heartbeat", "stats": hub.stats_dict()}))
                    continue
                last_frame_id, jpeg = result
                serialize_started_ns = _now_ns()
                payload = {
                    "type": "frame",
                    "frame_id": last_frame_id,
                    "image": {
                        "format": "jpeg",
                        "data_base64": base64.b64encode(jpeg).decode("ascii"),
                    },
                    "stats": hub.stats_dict(),
                }
                message = json.dumps(payload)
                serialize_finished_ns = _now_ns()
                send_started_ns = serialize_finished_ns
                _send_ws_text(self.connection, message)
                send_finished_ns = _now_ns()
                hub.mark_output(
                    serialize_ms=(serialize_finished_ns - serialize_started_ns) / 1_000_000,
                    send_ms=(send_finished_ns - send_started_ns) / 1_000_000,
                )
        except (BrokenPipeError, ConnectionError, OSError):
            return
        finally:
            self.server.runtime.run(hub.remove_websocket_client())


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    use_test_backend: bool = False,
    target_fps: int = 30,
) -> MonitorHTTPServer:
    return MonitorHTTPServer((host, port), MonitorRuntime(use_test_backend=use_test_backend, target_fps=target_fps))


def _send_ws_text(connection: Any, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    connection.sendall(bytes(header) + payload)


def _now_ns() -> int:
    import time

    return time.perf_counter_ns()
