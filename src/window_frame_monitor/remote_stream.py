from __future__ import annotations

import argparse
import base64
import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from time import perf_counter, perf_counter_ns, sleep
from typing import BinaryIO
from urllib.parse import urlparse

from PIL import Image

from window_frame_monitor.backends.base import CaptureBackend
from window_frame_monitor.backends.dxgi import DxgiDesktopDuplicationBackend
from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.backends.windows import WindowsWindowCaptureBackend
from window_frame_monitor.models import CapturedFrame, WindowInfo
from window_frame_monitor.video import H264Settings, build_ffmpeg_h264_command, build_ffmpeg_h264_decoder_command
from window_frame_monitor.windows import list_windows, resolve_window


STREAM_MAGIC = "WFH264/1"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_VLM_MODEL = "qwen3-vl:8b-instruct"
DEFAULT_VLM_SYSTEM_PROMPT = (
    "您好！辛苦啦！"
    "请问，可以请您作为一名视觉描述家，尝试描述眼前的游戏场景吗？描述可基于上下文想象其可能的行为过渡，描述文本限制在 20 字。"
    "同时，请问，如若您作为游戏控制者，接下来您可能会进行哪些操作呢？"
    "背景信息为：
        · 游戏背景：游戏名称为“我的世界”，是一款“沙盒游戏”，玩家可以在游戏中自由建造和探索。
        · 按键目标：
            · W --- 向前移动；
            · A --- 向左移动；
            · S --- 向后移动；
            · D --- 向右移动；
            · 空格 --- 跳跃；
            · e --- 打开物品栏；
            · 数字按键 1 - 9 --- 切换“手持物品”；
            · 鼠标右键 --- 使用物品；
            · 鼠标左键 --- 使用当前手持物品破坏目标；

    "期望目标为:破坏掉眼前的树木方块"
    "返回格式为："
    "· [描述文本]"
    "· [计划未来 15 份“时间步”的按键操作（仅返回操作按键）]"

)


@dataclass(frozen=True)
class FrameChange:
    change_score: float
    changed_pixels_ratio: float


def make_stream_header(settings: H264Settings) -> bytes:
    payload = {
        "magic": STREAM_MAGIC,
        "width": settings.width,
        "height": settings.height,
        "fps": settings.fps,
        "bitrate_kbps": settings.bitrate_kbps,
        "encoder": settings.encoder,
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def parse_stream_header(header: bytes) -> H264Settings:
    payload = json.loads(header.decode("utf-8").strip())
    if payload.get("magic") != STREAM_MAGIC:
        raise ValueError("Invalid H.264 stream header.")
    return H264Settings(
        width=int(payload["width"]),
        height=int(payload["height"]),
        fps=int(payload["fps"]),
        bitrate_kbps=int(payload["bitrate_kbps"]),
        encoder=str(payload.get("encoder", "h264_nvenc")),
    )


class FrameChangeDetector:
    def __init__(self, *, width: int, height: int, sample_width: int = 160, sample_height: int = 90) -> None:
        self._width = width
        self._height = height
        self._sample_size = (sample_width, sample_height)
        self._previous: bytes | None = None

    def update(self, rgb_bytes: bytes) -> FrameChange:
        sample = _sample_luma(rgb_bytes, self._width, self._height, self._sample_size)
        return self.update_luma(sample)

    def update_luma(self, luma_bytes: bytes) -> FrameChange:
        sample = _resize_luma(luma_bytes, self._width, self._height, self._sample_size)
        if self._previous is None:
            self._previous = sample
            return FrameChange(change_score=1.0, changed_pixels_ratio=1.0)

        total_delta = 0
        changed = 0
        for current, previous in zip(sample, self._previous):
            delta = abs(current - previous)
            total_delta += delta
            if delta > 18:
                changed += 1
        self._previous = sample
        pixels = max(1, len(sample))
        return FrameChange(
            change_score=total_delta / (pixels * 255),
            changed_pixels_ratio=changed / pixels,
        )


class RemoteFrameStore:
    def __init__(
        self,
        *,
        output_dir: str | Path,
        width: int,
        height: int,
        change_fps: float = 10.0,
        snapshot_fps: float = 1.0,
        state_fps: float = 2.0,
        pixel_format: str = "rgb24",
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._width = width
        self._height = height
        self._pixel_format = pixel_format
        self._detector = FrameChangeDetector(width=width, height=height)
        self._change_interval_s = _fps_to_interval(change_fps)
        self._snapshot_interval_s = _fps_to_interval(snapshot_fps)
        self._state_interval_s = _fps_to_interval(state_fps)
        self._last_change_at = 0.0
        self._last_snapshot_at = 0.0
        self._last_state_at = 0.0
        self._last_change = FrameChange(change_score=0.0, changed_pixels_ratio=0.0)
        self._latest_frame: bytes | None = None
        self._lock = threading.Lock()
        self._frame_times: deque[float] = deque()
        self._processed_times: deque[float] = deque()
        self._state: dict[str, object] = {
            "status": "waiting",
            "frame_count": 0,
            "decode_fps": 0.0,
            "processed_fps": 0.0,
            "processed_frame_count": 0,
            "change_score": 0.0,
            "changed_pixels_ratio": 0.0,
            "width": width,
            "height": height,
            "pixel_format": pixel_format,
            "latest_frame_path": str(self._output_dir / "latest.jpg"),
            "state_path": str(self._output_dir / "state.json"),
        }
        self._write_state_locked()

    def update_status(self, status: str, detail: str | None = None) -> None:
        with self._lock:
            self._state["status"] = status
            if detail:
                self._state["detail"] = detail
            self._write_state_locked()

    def update_frame(self, rgb_bytes: bytes) -> None:
        now = perf_counter()
        changed = self._last_change
        processed = False
        if self._should_run(self._last_change_at, self._change_interval_s, now):
            changed = self._detect_change(rgb_bytes)
            self._last_change = changed
            self._last_change_at = now
            processed = True
        with self._lock:
            self._latest_frame = rgb_bytes
            self._frame_times.append(now)
            while self._frame_times and now - self._frame_times[0] > 1.0:
                self._frame_times.popleft()
            if processed:
                self._processed_times.append(now)
                while self._processed_times and now - self._processed_times[0] > 1.0:
                    self._processed_times.popleft()
            frame_count = int(self._state["frame_count"]) + 1
            processed_frame_count = int(self._state["processed_frame_count"]) + (1 if processed else 0)
            self._state.update(
                {
                    "status": "receiving",
                    "frame_count": frame_count,
                    "decode_fps": float(len(self._frame_times)),
                    "processed_fps": float(len(self._processed_times)),
                    "processed_frame_count": processed_frame_count,
                    "change_score": changed.change_score,
                    "changed_pixels_ratio": changed.changed_pixels_ratio,
                    "last_frame_time_ns": perf_counter_ns(),
                }
            )
            if self._should_run(self._last_snapshot_at, self._snapshot_interval_s, now):
                _write_jpeg(self._output_dir / "latest.jpg", self._to_rgb(rgb_bytes), self._width, self._height)
                self._last_snapshot_at = now
            if self._should_run(self._last_state_at, self._state_interval_s, now):
                self._write_state_locked()
                self._last_state_at = now

    def state(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            frame = self._latest_frame
        if frame is not None:
            return _encode_jpeg(self._to_rgb(frame), self._width, self._height)
        path = self._output_dir / "latest.jpg"
        return path.read_bytes() if path.exists() else None

    def _detect_change(self, frame_bytes: bytes) -> FrameChange:
        if self._pixel_format == "yuv420p":
            return self._detector.update_luma(frame_bytes[: self._width * self._height])
        return self._detector.update(frame_bytes)

    def _to_rgb(self, frame_bytes: bytes) -> bytes:
        if self._pixel_format == "yuv420p":
            return _yuv420p_to_rgb(frame_bytes, self._width, self._height)
        return frame_bytes

    def _write_state_locked(self) -> None:
        (self._output_dir / "state.json").write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    @staticmethod
    def _should_run(last_run_at: float, interval_s: float | None, now: float) -> bool:
        return interval_s is not None and (last_run_at == 0.0 or now - last_run_at >= interval_s)


class RemoteH264Receiver:
    def __init__(
        self,
        *,
        stream_host: str = "0.0.0.0",
        stream_port: int = 8766,
        dashboard_host: str = "127.0.0.1",
        dashboard_port: int = 8770,
        output_dir: str | Path = "runtime/remote_receiver",
        decoder: str = "software",
        change_fps: float = 10.0,
        snapshot_fps: float = 1.0,
        state_fps: float = 2.0,
        pixel_format: str = "yuv420p",
        ollama_url: str = DEFAULT_OLLAMA_URL,
        vlm_model: str = DEFAULT_VLM_MODEL,
        vlm_context_tokens: int = 40_000,
        vlm_max_output_tokens: int = 50,
        vlm_system_prompt: str = DEFAULT_VLM_SYSTEM_PROMPT,
    ) -> None:
        self.stream_host = stream_host
        self.stream_port = stream_port
        self.dashboard_host = dashboard_host
        self.dashboard_port = dashboard_port
        self.output_dir = Path(output_dir)
        self._decoder = decoder
        self._change_fps = change_fps
        self._snapshot_fps = snapshot_fps
        self._state_fps = state_fps
        self._pixel_format = pixel_format
        self._ollama_url = ollama_url.rstrip("/")
        self._vlm_model = vlm_model
        self._vlm_context_tokens = max(1024, int(vlm_context_tokens))
        self._vlm_max_output_tokens = max(1, int(vlm_max_output_tokens))
        self._vlm_system_prompt = vlm_system_prompt
        self._vlm_lock = threading.Lock()
        self._vlm_messages: list[dict[str, object]] = []
        self._vlm_history: deque[dict[str, object]] = deque(maxlen=80)
        self._store = RemoteFrameStore(
            output_dir=self.output_dir,
            width=16,
            height=16,
            change_fps=change_fps,
            snapshot_fps=snapshot_fps,
            state_fps=state_fps,
            pixel_format=pixel_format,
        )
        self._stream_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._network_times: deque[tuple[float, int]] = deque()
        self._received_total_bytes = 0
        self._network_lock = threading.Lock()

    def start_stream_listener(self) -> None:
        self._stream_thread = threading.Thread(target=self._listen_for_streams, name="h264-stream-listener", daemon=True)
        self._stream_thread.start()

    def serve_dashboard(self) -> None:
        server = _ReceiverDashboardServer((self.dashboard_host, self.dashboard_port), self)
        print(f"Remote receiver dashboard at http://{self.dashboard_host}:{self.dashboard_port}/")
        print(f"H.264 stream listening on tcp://{self.stream_host}:{self.stream_port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            server.server_close()

    def state(self) -> dict[str, object]:
        state = self._store.state()
        state["received_kib_per_s"] = self.received_kib_per_s()
        state["received_total_kib"] = self.received_total_kib()
        return state

    def latest_jpeg(self) -> bytes | None:
        return self._store.latest_jpeg()

    def describe_latest_frame(self) -> dict[str, object]:
        jpeg = self.latest_jpeg()
        if jpeg is None:
            return self._record_vlm_history("error", "No frame available yet.")
        started = perf_counter()
        try:
            description = self._call_ollama_vlm(jpeg)
        except Exception as exc:
            return self._record_vlm_history("error", str(exc))
        elapsed_ms = (perf_counter() - started) * 1000
        return self._record_vlm_history("ok", description, elapsed_ms=elapsed_ms)

    def vlm_history(self) -> list[dict[str, object]]:
        with self._vlm_lock:
            return list(self._vlm_history)

    def _call_ollama_vlm(self, jpeg: bytes) -> str:
        user_content = "请简洁描述当前画面中的事物与关键内容。"
        image_b64 = base64.b64encode(jpeg).decode("ascii")
        with self._vlm_lock:
            messages = [
                {"role": "system", "content": self._vlm_system_prompt},
                *self._vlm_messages,
                {"role": "user", "content": user_content, "images": [image_b64]},
            ]
        payload = {
            "model": self._vlm_model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": self._vlm_context_tokens,
                "num_predict": self._vlm_max_output_tokens,
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
            raise RuntimeError("Ollama returned an empty description.")
        with self._vlm_lock:
            self._vlm_messages.append({"role": "user", "content": user_content})
            self._vlm_messages.append({"role": "assistant", "content": content})
            self._trim_vlm_messages_locked()
        return content

    def _record_vlm_history(
        self,
        status: str,
        content: str,
        *,
        elapsed_ms: float | None = None,
    ) -> dict[str, object]:
        event: dict[str, object] = {
            "status": status,
            "content": content,
            "elapsed_ms": elapsed_ms,
            "time_ns": perf_counter_ns(),
        }
        with self._vlm_lock:
            self._vlm_history.append(event)
        return event

    def _trim_vlm_messages_locked(self) -> None:
        max_chars = self._vlm_context_tokens * 4
        while len(json.dumps(self._vlm_messages, ensure_ascii=False)) > max_chars and len(self._vlm_messages) > 2:
            del self._vlm_messages[:2]

    def _listen_for_streams(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.stream_host, self.stream_port))
            listener.listen(1)
            listener.settimeout(0.5)
            self._store.update_status("listening")
            while not self._stop.is_set():
                try:
                    connection, address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with connection:
                    self._handle_connection(connection, address)

    def _handle_connection(self, connection: socket.socket, address: tuple[str, int]) -> None:
        source = connection.makefile("rb")
        try:
            header = source.readline(8192)
            settings = parse_stream_header(header)
        except Exception as exc:
            self._store.update_status("header-error", str(exc))
            return

        self._store = RemoteFrameStore(
            output_dir=self.output_dir,
            width=settings.width,
            height=settings.height,
            change_fps=self._change_fps,
            snapshot_fps=self._snapshot_fps,
            state_fps=self._state_fps,
            pixel_format=self._pixel_format,
        )
        self._store.update_status("connected", f"{address[0]}:{address[1]}")
        decoder = subprocess.Popen(
            build_ffmpeg_h264_decoder_command(settings, decoder=self._decoder, pixel_format=self._pixel_format),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert decoder.stdin is not None
        assert decoder.stdout is not None
        assert decoder.stderr is not None
        decoder_errors: deque[str] = deque(maxlen=6)
        stderr_thread = threading.Thread(
            target=_collect_process_stderr,
            args=(decoder.stderr, decoder_errors),
            daemon=True,
        )
        stderr_thread.start()
        pump_thread = threading.Thread(
            target=_pump_socket_to_decoder,
            args=(source, decoder.stdin, self.mark_received_bytes),
            daemon=True,
        )
        pump_thread.start()
        frame_size = decoded_frame_size(width=settings.width, height=settings.height, pixel_format=self._pixel_format)
        disconnect_detail = "stream ended"
        try:
            while not self._stop.is_set():
                raw = _read_exact(decoder.stdout, frame_size)
                if raw is None:
                    returncode = _wait_process_returncode(decoder, timeout_s=0.5)
                    stderr_detail = _join_recent_errors(decoder_errors)
                    if returncode is not None and returncode != 0:
                        disconnect_detail = f"decoder exited with code {returncode}"
                        if stderr_detail:
                            disconnect_detail = f"{disconnect_detail}: {stderr_detail}"
                    elif stderr_detail:
                        disconnect_detail = f"decoder ended: {stderr_detail}"
                    break
                self._store.update_frame(raw)
        except Exception as exc:
            disconnect_detail = str(exc)
            raise
        finally:
            _close_process(decoder)
            pump_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            self._store.update_status("disconnected", disconnect_detail)

    def mark_received_bytes(self, byte_count: int) -> None:
        now = perf_counter()
        with self._network_lock:
            self._received_total_bytes += byte_count
            self._network_times.append((now, byte_count))
            while self._network_times and now - self._network_times[0][0] > 1.0:
                self._network_times.popleft()

    def received_kib_per_s(self) -> float:
        now = perf_counter()
        with self._network_lock:
            while self._network_times and now - self._network_times[0][0] > 1.0:
                self._network_times.popleft()
            return sum(byte_count for _timestamp, byte_count in self._network_times) / 1024

    def received_total_kib(self) -> float:
        with self._network_lock:
            return self._received_total_bytes / 1024


class _ReceiverDashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], receiver: RemoteH264Receiver) -> None:
        self.receiver = receiver
        super().__init__(server_address, _ReceiverDashboardHandler)


class _ReceiverDashboardHandler(BaseHTTPRequestHandler):
    server: _ReceiverDashboardServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_bytes(_dashboard_html(), "text/html; charset=utf-8")
        elif path == "/state":
            self._send_json(self.server.receiver.state())
        elif path == "/vlm-history":
            self._send_json(self.server.receiver.vlm_history())
        elif path == "/latest.jpg":
            jpeg = self.server.receiver.latest_jpeg()
            if jpeg is None:
                self.send_error(HTTPStatus.NOT_FOUND, "No frame available yet.")
                return
            self._send_bytes(jpeg, "image/jpeg")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/describe-latest":
            self._send_json(self.server.receiver.describe_latest_frame())
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _send_json(self, value: object) -> None:
        self._send_bytes(json.dumps(value).encode("utf-8"), "application/json; charset=utf-8")

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def run_sender(
    *,
    server_host: str,
    stream_port: int,
    target: WindowInfo,
    target_fps: int,
    bitrate_kbps: int,
    encoder: str,
    output_width: int | None = None,
    output_height: int | None = None,
    duration_s: float | None = None,
    use_test_backend: bool = False,
) -> None:
    backend = _select_sender_backend(use_test_backend=use_test_backend)
    width = output_width or target.width
    height = output_height or target.height
    settings = H264Settings(width=width, height=height, fps=target_fps, bitrate_kbps=bitrate_kbps, encoder=encoder)
    encoder_process = subprocess.Popen(
        build_ffmpeg_h264_command(settings),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert encoder_process.stdin is not None
    assert encoder_process.stdout is not None
    assert encoder_process.stderr is not None
    encoder_errors: deque[str] = deque(maxlen=8)
    stderr_thread = threading.Thread(
        target=_collect_process_stderr,
        args=(encoder_process.stderr, encoder_errors),
        daemon=True,
    )
    stderr_thread.start()
    backend.start(target)
    sent_frames = 0
    print(
        f"Sending H.264 stream to {server_host}:{stream_port} "
        f"at {settings.width}x{settings.height} {settings.fps}fps using {settings.encoder}."
    )
    with socket.create_connection((server_host, stream_port), timeout=10) as connection:
        connection.sendall(make_stream_header(settings))
        pump_thread = threading.Thread(target=_pump_encoder_to_socket, args=(encoder_process.stdout, connection), daemon=True)
        pump_thread.start()
        started = perf_counter()
        interval_s = 1 / max(1, target_fps)
        try:
            while duration_s is None or perf_counter() - started < duration_s:
                loop_started = perf_counter()
                frame = backend.get_frame()
                rgb_bytes = _resize_rgb(frame, settings.width, settings.height)
                try:
                    encoder_process.stdin.write(rgb_bytes)
                    encoder_process.stdin.flush()
                except BrokenPipeError:
                    error_detail = _join_recent_errors(encoder_errors)
                    if error_detail:
                        print(f"Encoder stopped accepting frames: {error_detail}")
                    else:
                        print("Encoder stopped accepting frames.")
                    break
                sent_frames += 1
                elapsed = perf_counter() - loop_started
                sleep(max(0.0, interval_s - elapsed))
        finally:
            backend.stop()
            try:
                encoder_process.stdin.close()
            except OSError:
                pass
            pump_thread.join(timeout=3)
            _close_process(encoder_process)
            stderr_thread.join(timeout=1)
            error_detail = _join_recent_errors(encoder_errors)
            if error_detail:
                print(f"Encoder detail: {error_detail}")
            print(f"Sender stopped after {sent_frames} frames.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal H.264 remote stream sender/receiver.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    receiver = subparsers.add_parser("receiver", help="Receive and decode an H.264 stream.")
    receiver.add_argument("--stream-host", default="0.0.0.0")
    receiver.add_argument("--stream-port", type=int, default=8766)
    receiver.add_argument("--dashboard-host", default="127.0.0.1")
    receiver.add_argument("--dashboard-port", type=int, default=8770)
    receiver.add_argument("--output-dir", default="runtime/remote_receiver")
    receiver.add_argument("--decoder", default="software", choices=["software", "cuda"])
    receiver.add_argument("--change-fps", type=float, default=10.0)
    receiver.add_argument("--snapshot-fps", type=float, default=1.0)
    receiver.add_argument("--state-fps", type=float, default=2.0)
    receiver.add_argument("--frame-format", default="yuv420p", choices=["yuv420p", "rgb24"])
    receiver.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    receiver.add_argument("--vlm-model", default=DEFAULT_VLM_MODEL)
    receiver.add_argument("--vlm-context-tokens", type=int, default=40_000)
    receiver.add_argument("--vlm-max-output-tokens", type=int, default=50)
    receiver.add_argument("--vlm-system-prompt", default=DEFAULT_VLM_SYSTEM_PROMPT)

    sender = subparsers.add_parser("sender", help="Capture a window and push H.264 to a receiver.")
    sender.add_argument("--server-host", required=True)
    sender.add_argument("--stream-port", type=int, default=8766)
    sender.add_argument("--hwnd", type=int)
    sender.add_argument("--title")
    sender.add_argument("--process-name")
    sender.add_argument("--test-backend", action="store_true")
    sender.add_argument("--fps", type=int, default=24)
    sender.add_argument("--bitrate-kbps", type=int, default=4000)
    sender.add_argument("--encoder", default="h264_nvenc", choices=["h264_nvenc", "libx264"])
    sender.add_argument("--width", type=int)
    sender.add_argument("--height", type=int)
    sender.add_argument("--duration-s", type=float)

    lister = subparsers.add_parser("list-windows", help="List capturable windows as JSON.")
    lister.add_argument("--test-backend", action="store_true")

    args = parser.parse_args()
    if args.command == "receiver":
        app = RemoteH264Receiver(
            stream_host=args.stream_host,
            stream_port=args.stream_port,
            dashboard_host=args.dashboard_host,
            dashboard_port=args.dashboard_port,
            output_dir=args.output_dir,
            decoder=args.decoder,
            change_fps=args.change_fps,
            snapshot_fps=args.snapshot_fps,
            state_fps=args.state_fps,
            pixel_format=args.frame_format,
            ollama_url=args.ollama_url,
            vlm_model=args.vlm_model,
            vlm_context_tokens=args.vlm_context_tokens,
            vlm_max_output_tokens=args.vlm_max_output_tokens,
            vlm_system_prompt=args.vlm_system_prompt,
        )
        app.start_stream_listener()
        app.serve_dashboard()
    elif args.command == "sender":
        target = _resolve_sender_target(args)
        run_sender(
            server_host=args.server_host,
            stream_port=args.stream_port,
            target=target,
            target_fps=args.fps,
            bitrate_kbps=args.bitrate_kbps,
            encoder=args.encoder,
            output_width=args.width,
            output_height=args.height,
            duration_s=args.duration_s,
            use_test_backend=args.test_backend,
        )
    elif args.command == "list-windows":
        windows = [WindowInfo(hwnd=0, title="Test Pattern", process_name="test-pattern", width=640, height=360)] if args.test_backend else list_windows()
        print(json.dumps([asdict(window) for window in windows], indent=2, ensure_ascii=False))


def _resolve_sender_target(args: argparse.Namespace) -> WindowInfo:
    if args.test_backend:
        return WindowInfo(hwnd=0, title="Test Pattern", process_name="test-pattern", width=640, height=360)
    return resolve_window(hwnd=args.hwnd, title=args.title, process_name=args.process_name)


def _select_sender_backend(*, use_test_backend: bool) -> CaptureBackend:
    backends: list[CaptureBackend] = (
        [TestPatternBackend()]
        if use_test_backend
        else [DxgiDesktopDuplicationBackend(), WindowsWindowCaptureBackend(), TestPatternBackend()]
    )
    reasons = []
    for backend in backends:
        available, reason = backend.is_available()
        if available:
            return backend
        if reason:
            reasons.append(f"{backend.name}: {reason}")
    raise RuntimeError("; ".join(reasons) or "No sender capture backend is available.")


def _pump_socket_to_decoder(source: BinaryIO, destination: BinaryIO, on_chunk: object | None = None) -> None:
    try:
        while True:
            read1 = getattr(source, "read1", None)
            chunk = read1(4096) if read1 else source.read(4096)
            if not chunk:
                break
            if callable(on_chunk):
                on_chunk(len(chunk))
            destination.write(chunk)
            destination.flush()
    except OSError:
        pass
    finally:
        try:
            destination.close()
        except OSError:
            pass


def _collect_process_stderr(source: BinaryIO, messages: deque[str]) -> None:
    for raw_line in iter(source.readline, b""):
        line = raw_line.decode("utf-8", errors="replace").strip()
        if line:
            messages.append(line)


def _join_recent_errors(messages: deque[str]) -> str:
    return " | ".join(messages)


def _wait_process_returncode(process: subprocess.Popen[bytes], timeout_s: float) -> int | None:
    try:
        return process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return process.poll()


def _pump_encoder_to_socket(source: BinaryIO, connection: socket.socket) -> None:
    try:
        while True:
            read1 = getattr(source, "read1", None)
            chunk = read1(4096) if read1 else source.read(4096)
            if not chunk:
                break
            connection.sendall(chunk)
    except OSError:
        pass


def _read_exact(source: BinaryIO, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = source.read(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _resize_rgb(frame: CapturedFrame, width: int, height: int) -> bytes:
    if frame.width == width and frame.height == height:
        return frame.rgb_bytes
    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb_bytes)
    return image.resize((width, height), Image.Resampling.BILINEAR).tobytes()


def _sample_luma(rgb_bytes: bytes, width: int, height: int, sample_size: tuple[int, int]) -> bytes:
    image = Image.frombytes("RGB", (width, height), rgb_bytes)
    image = image.resize(sample_size, Image.Resampling.BILINEAR).convert("L")
    return image.tobytes()


def _resize_luma(luma_bytes: bytes, width: int, height: int, sample_size: tuple[int, int]) -> bytes:
    image = Image.frombytes("L", (width, height), luma_bytes)
    return image.resize(sample_size, Image.Resampling.BILINEAR).tobytes()


def _yuv420p_to_rgb(yuv_bytes: bytes, width: int, height: int) -> bytes:
    y_size = width * height
    uv_width = width // 2
    uv_height = height // 2
    uv_size = uv_width * uv_height
    y = Image.frombytes("L", (width, height), yuv_bytes[:y_size])
    u = Image.frombytes("L", (uv_width, uv_height), yuv_bytes[y_size : y_size + uv_size])
    v = Image.frombytes("L", (uv_width, uv_height), yuv_bytes[y_size + uv_size : y_size + uv_size * 2])
    u = u.resize((width, height), Image.Resampling.BILINEAR)
    v = v.resize((width, height), Image.Resampling.BILINEAR)
    return Image.merge("YCbCr", (y, u, v)).convert("RGB").tobytes()


def _write_jpeg(path: Path, rgb_bytes: bytes, width: int, height: int) -> None:
    path.write_bytes(_encode_jpeg(rgb_bytes, width, height))


def _encode_jpeg(rgb_bytes: bytes, width: int, height: int) -> bytes:
    image = Image.frombytes("RGB", (width, height), rgb_bytes)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def _fps_to_interval(fps: float) -> float | None:
    return None if fps <= 0 else 1 / fps


def decoded_frame_size(*, width: int, height: int, pixel_format: str) -> int:
    if pixel_format == "yuv420p":
        return width * height * 3 // 2
    if pixel_format == "rgb24":
        return width * height * 3
    raise ValueError(f"Unsupported decoded pixel format: {pixel_format}")


def _close_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()


def _dashboard_html() -> bytes:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Remote H.264 Receiver</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; background: #101216; color: #f5f7fb; }
    body { margin: 0; }
    main { min-height: 100vh; display: grid; grid-template-rows: auto 1fr auto auto; }
    header, section { padding: 16px 18px; border-bottom: 1px solid #29313d; }
    h1 { margin: 0; font-size: 20px; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }
    .metric { border: 1px solid #29313d; border-radius: 6px; padding: 12px; background: #0b0e13; }
    .metric span { display: block; color: #aeb7c6; font-size: 12px; }
    .metric strong { display: block; margin-top: 4px; font-size: 20px; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .content { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 14px; align-items: stretch; }
    .preview { display: grid; place-items: center; background: #07090d; min-height: 320px; border: 1px solid #29313d; border-radius: 6px; }
    .llm-panel { border: 1px solid #29313d; border-radius: 6px; background: #0b0e13; padding: 12px; min-height: 320px; display: grid; grid-template-rows: auto 1fr; gap: 10px; }
    .llm-panel h2 { margin: 0; font-size: 14px; color: #dce6f5; }
    .llm-history { height: 300px; overflow-y: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: #c9d3e1; font: 12px/1.45 Consolas, monospace; }
    .llm-entry { border-top: 1px solid #202734; padding: 10px 0; }
    .llm-entry:first-child { border-top: 0; padding-top: 0; }
    .llm-meta { color: #8ea0b8; font-size: 11px; margin-bottom: 4px; }
    .llm-error { color: #ffb4a8; }
    img { max-width: 100%; max-height: 70vh; object-fit: contain; }
    button { background: #2f6feb; border: 1px solid #4f8cff; border-radius: 6px; color: white; min-height: 36px; padding: 0 14px; }
    button:disabled { opacity: 0.55; cursor: wait; }
    pre { margin: 0; white-space: pre-wrap; color: #c9d3e1; font: 12px/1.45 Consolas, monospace; }
    @media (max-width: 900px) { .content { grid-template-columns: 1fr; } .llm-history { height: 220px; } }
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Remote H.264 Receiver</h1>
      <p id="status">waiting</p>
      <div class="actions">
        <button id="get-latest" type="button">Get Latest Frame</button>
        <button id="describe-latest" type="button">Send Latest Frame to VLM</button>
      </div>
    </header>
    <section class="content">
      <div class="preview"><img id="latest" alt="Latest decoded frame" /></div>
      <aside class="llm-panel">
        <h2>LLM History</h2>
        <div id="llm-history" class="llm-history"></div>
      </aside>
    </section>
    <section>
      <div class="grid">
        <div class="metric"><span>Received</span><strong id="received">0.0 KiB/s</strong></div>
        <div class="metric"><span>Received Total</span><strong id="received-total">0.0 KiB</strong></div>
        <div class="metric"><span>Decoded Frames</span><strong id="frames">0</strong></div>
        <div class="metric"><span>Decoded FPS</span><strong id="fps">0.0</strong></div>
        <div class="metric"><span>Processed FPS</span><strong id="processed-fps">0.0</strong></div>
        <div class="metric"><span>Change Score</span><strong id="change">0.000</strong></div>
        <div class="metric"><span>Changed Pixels</span><strong id="changed">0.0%</strong></div>
        <div class="metric"><span>Resolution</span><strong id="resolution">0x0</strong></div>
      </div>
    </section>
    <section><pre id="raw"></pre></section>
  </main>
  <script>
    async function tick() {
      const state = await fetch('/state', { cache: 'no-store' }).then(r => r.json());
      document.querySelector('#status').textContent = state.status + (state.detail ? ' - ' + state.detail : '');
      document.querySelector('#received').textContent = Number(state.received_kib_per_s || 0).toFixed(1) + ' KiB/s';
      document.querySelector('#received-total').textContent = Number(state.received_total_kib || 0).toFixed(1) + ' KiB';
      document.querySelector('#frames').textContent = state.frame_count || 0;
      document.querySelector('#fps').textContent = Number(state.decode_fps || 0).toFixed(1);
      document.querySelector('#processed-fps').textContent = Number(state.processed_fps || 0).toFixed(1);
      document.querySelector('#change').textContent = Number(state.change_score || 0).toFixed(3);
      document.querySelector('#changed').textContent = (Number(state.changed_pixels_ratio || 0) * 100).toFixed(1) + '%';
      document.querySelector('#resolution').textContent = `${state.width || 0}x${state.height || 0}`;
      document.querySelector('#raw').textContent = JSON.stringify(state, null, 2);
      await refreshVlmHistory();
    }
    async function getLatestFrame() {
      const response = await fetch('/latest.jpg?t=' + Date.now(), { cache: 'no-store' });
      if (response.ok) {
        const blob = await response.blob();
        document.querySelector('#latest').src = URL.createObjectURL(blob);
      }
    }
    async function describeLatestFrame() {
      const button = document.querySelector('#describe-latest');
      button.disabled = true;
      button.textContent = 'Sending...';
      try {
        const response = await fetch('/describe-latest', { method: 'POST', cache: 'no-store' });
        await response.json();
        await refreshVlmHistory();
      } finally {
        button.disabled = false;
        button.textContent = 'Send Latest Frame to VLM';
      }
    }
    async function refreshVlmHistory() {
      const history = await fetch('/vlm-history', { cache: 'no-store' }).then(r => r.json());
      renderVlmHistory(history);
    }
    function renderVlmHistory(history) {
      const container = document.querySelector('#llm-history');
      if (!history.length) {
        container.textContent = 'No VLM descriptions yet.';
        return;
      }
      container.innerHTML = history.slice().reverse().map((entry, index) => {
        const elapsed = entry.elapsed_ms == null ? '' : ` · ${Number(entry.elapsed_ms).toFixed(0)} ms`;
        const content = escapeHtml(String(entry.content || ''));
        const cls = entry.status === 'ok' ? '' : ' llm-error';
        return `<div class="llm-entry${cls}"><div class="llm-meta">#${history.length - index} · ${entry.status}${elapsed}</div>${content}</div>`;
      }).join('');
    }
    function escapeHtml(value) {
      return value.replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }
    document.querySelector('#get-latest').addEventListener('click', getLatestFrame);
    document.querySelector('#describe-latest').addEventListener('click', describeLatestFrame);
    setInterval(tick, 500);
    tick();
  </script>
</body>
</html>""".encode("utf-8")


if __name__ == "__main__":
    main()
