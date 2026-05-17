from __future__ import annotations

import asyncio
from dataclasses import asdict
from io import BytesIO
from time import perf_counter_ns

from PIL import Image

from window_frame_monitor.backends.base import CaptureBackend
from window_frame_monitor.models import CapturedFrame, RuntimeStats, WindowInfo
from window_frame_monitor.stats import StatsTracker
from window_frame_monitor.video import H264Settings, available_h264_encoders


class FrameHub:
    def __init__(self, backends: list[CaptureBackend], target_fps: int = 30) -> None:
        self._backends = backends
        self._target_fps = target_fps
        self._target: WindowInfo | None = None
        self._backend: CaptureBackend | None = None
        self._backend_reason: str | None = None
        self._websocket_clients = 0
        self._mjpeg_clients = 0
        self._h264_probe_clients = 0
        self._stats = StatsTracker()
        self._stats.set_target_fps(target_fps)
        self._h264_settings = H264Settings(fps=target_fps)
        self._available_h264_encoders = available_h264_encoders()
        self._latest_frame: CapturedFrame | None = None
        self._latest_jpeg: bytes | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._frame_event = asyncio.Event()
        self._stopping = False

    async def set_target(self, window: WindowInfo) -> None:
        await self.stop_capture(clear_clients=True)
        async with self._lock:
            self._target = window
            self._select_backend_locked()
            if self._backend and self._consumers_locked():
                self._ensure_task_locked()
            self._refresh_stats_unlocked()

    async def add_websocket_client(self) -> None:
        async with self._lock:
            self._websocket_clients += 1
            self._ensure_task_locked()
            self._refresh_stats_unlocked()

    async def remove_websocket_client(self) -> None:
        async with self._lock:
            self._websocket_clients = max(0, self._websocket_clients - 1)
            self._refresh_stats_unlocked()

    async def add_mjpeg_client(self) -> None:
        async with self._lock:
            self._mjpeg_clients += 1
            self._ensure_task_locked()
            self._refresh_stats_unlocked()

    async def remove_mjpeg_client(self) -> None:
        async with self._lock:
            self._mjpeg_clients = max(0, self._mjpeg_clients - 1)
            self._refresh_stats_unlocked()

    async def add_h264_probe_client(self) -> None:
        async with self._lock:
            self._h264_probe_clients += 1
            self._ensure_task_locked()
            self._refresh_stats_unlocked()

    async def remove_h264_probe_client(self) -> None:
        async with self._lock:
            self._h264_probe_clients = max(0, self._h264_probe_clients - 1)
            self._refresh_stats_unlocked()

    async def latest_jpeg(self) -> bytes | None:
        if self._latest_jpeg is None:
            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=1.0)
            except TimeoutError:
                return None
        return self._latest_jpeg

    async def wait_for_next_jpeg(self, after_frame_id: int = 0) -> tuple[int, bytes] | None:
        for _ in range(30):
            if self._latest_frame and self._latest_jpeg and self._latest_frame.frame_id > after_frame_id:
                return self._latest_frame.frame_id, self._latest_jpeg
            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=1.0)
            except TimeoutError:
                return None
            self._frame_event.clear()
        return None

    async def wait_for_next_frame(self, after_frame_id: int = 0) -> tuple[int, CapturedFrame] | None:
        for _ in range(30):
            if self._latest_frame and self._latest_frame.frame_id > after_frame_id:
                return self._latest_frame.frame_id, self._latest_frame
            try:
                await asyncio.wait_for(self._frame_event.wait(), timeout=1.0)
            except TimeoutError:
                return None
            self._frame_event.clear()
        return None

    def snapshot_stats(self) -> RuntimeStats:
        self._refresh_stats_unlocked()
        return self._stats.snapshot()

    def stats_dict(self) -> dict[str, object]:
        return asdict(self.snapshot_stats())

    @property
    def h264_settings(self) -> H264Settings:
        return self._h264_settings

    @property
    def has_target(self) -> bool:
        return self._target is not None

    def mark_output(self, *, serialize_ms: float = 0.0, send_ms: float = 0.0) -> None:
        self._stats.mark_output(serialize_ms=serialize_ms, send_ms=send_ms)

    async def set_target_fps(self, target_fps: int) -> None:
        if target_fps < 1 or target_fps > 240:
            raise ValueError("target_fps must be between 1 and 240.")
        async with self._lock:
            self._target_fps = target_fps
            self._stats.set_target_fps(target_fps)
            self._h264_settings = H264Settings(
                width=self._h264_settings.width,
                height=self._h264_settings.height,
                fps=target_fps,
                bitrate_kbps=self._h264_settings.bitrate_kbps,
                encoder=self._h264_settings.encoder,
                preset=self._h264_settings.preset,
            )
            self._refresh_stats_unlocked()

    async def set_h264_settings(
        self,
        *,
        encoder: str | None = None,
        bitrate_kbps: int | None = None,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        async with self._lock:
            self._h264_settings = H264Settings(
                width=width if width is not None else self._h264_settings.width,
                height=height if height is not None else self._h264_settings.height,
                fps=self._target_fps,
                bitrate_kbps=bitrate_kbps if bitrate_kbps is not None else self._h264_settings.bitrate_kbps,
                encoder=encoder if encoder is not None else self._h264_settings.encoder,
                preset=self._h264_settings.preset,
            )
            self._refresh_stats_unlocked()

    async def match_h264_output_to_source(self) -> None:
        async with self._lock:
            width = self._latest_frame.width if self._latest_frame else self._target.width if self._target else 0
            height = self._latest_frame.height if self._latest_frame else self._target.height if self._target else 0
            if width <= 0 or height <= 0:
                raise ValueError("No source size is available. Select and capture a target first.")
            self._h264_settings = H264Settings(
                width=width,
                height=height,
                fps=self._target_fps,
                bitrate_kbps=self._h264_settings.bitrate_kbps,
                encoder=self._h264_settings.encoder,
                preset=self._h264_settings.preset,
            )
            self._refresh_stats_unlocked()

    async def refresh_capture(self) -> None:
        await self.stop_capture(clear_clients=True)
        async with self._lock:
            self._latest_frame = None
            self._latest_jpeg = None
            self._frame_event.clear()
            self._refresh_stats_unlocked()

    async def stop_capture(self, *, clear_clients: bool = False) -> None:
        task: asyncio.Task[None] | None
        async with self._lock:
            self._stopping = True
            if clear_clients:
                self._websocket_clients = 0
                self._mjpeg_clients = 0
                self._h264_probe_clients = 0
            task = self._capture_task
            self._capture_task = None
            self._latest_frame = None
            self._latest_jpeg = None
            self._frame_event.clear()
            self._refresh_stats_unlocked()

        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def close(self) -> None:
        await self.stop_capture(clear_clients=True)
        if self._backend:
            self._backend.stop()

    def _select_backend_locked(self) -> None:
        self._backend = None
        self._backend_reason = None
        unavailable_reasons: list[str] = []
        for backend in self._backends:
            available, reason = backend.is_available()
            if available:
                self._backend = backend
                self._backend_reason = "; ".join(unavailable_reasons) if unavailable_reasons else None
                return
            if reason:
                unavailable_reasons.append(f"{backend.name}: {reason}")
        self._backend_reason = "; ".join(unavailable_reasons) if unavailable_reasons else "No capture backend is available."

    def _ensure_task_locked(self) -> None:
        if self._target is None:
            return
        if self._backend is None:
            self._select_backend_locked()
        if self._backend is None:
            return
        if self._capture_task is None or self._capture_task.done():
            self._stopping = False
            self._capture_task = asyncio.create_task(self._capture_loop())

    async def _capture_loop(self) -> None:
        assert self._backend is not None
        assert self._target is not None
        backend_name = self._backend.name
        self._backend.start(self._target)
        interval_s = 1 / max(1, self._target_fps)
        try:
            while not self._stopping:
                if not self._consumers_unlocked():
                    await asyncio.sleep(0.05)
                    self._refresh_stats_unlocked()
                    continue
                started_ns = perf_counter_ns()
                capture_started_ns = started_ns
                frame = await asyncio.to_thread(self._backend.get_frame)
                capture_finished_ns = perf_counter_ns()
                encode_started_ns = capture_finished_ns
                jpeg = await asyncio.to_thread(_encode_jpeg, frame)
                encode_finished_ns = perf_counter_ns()
                finished_ns = perf_counter_ns()
                self._latest_frame = frame
                self._latest_jpeg = jpeg
                self._stats.mark_frame(
                    frame.frame_id,
                    started_ns,
                    finished_ns,
                    capture_ms=(capture_finished_ns - capture_started_ns) / 1_000_000,
                    encode_ms=(encode_finished_ns - encode_started_ns) / 1_000_000,
                    serialize_ms=0.0,
                    send_ms=0.0,
                    reused_frame=frame.reused,
                )
                self._refresh_stats_unlocked()
                self._frame_event.set()
                elapsed_s = (perf_counter_ns() - started_ns) / 1_000_000_000
                await asyncio.sleep(max(0.0, interval_s - elapsed_s))
        except Exception as exc:
            self._backend_reason = f"{backend_name} capture failed: {exc}"
            self._websocket_clients = 0
            self._mjpeg_clients = 0
            self._refresh_stats_unlocked()
        finally:
            self._backend.stop()

    def _consumers_locked(self) -> bool:
        return self._websocket_clients > 0 or self._mjpeg_clients > 0 or self._h264_probe_clients > 0

    def _consumers_unlocked(self) -> bool:
        return self._websocket_clients > 0 or self._mjpeg_clients > 0 or self._h264_probe_clients > 0

    def _refresh_stats_unlocked(self) -> None:
        active_pipelines = []
        if self._websocket_clients:
            active_pipelines.append("websocket")
        if self._mjpeg_clients:
            active_pipelines.append("mjpeg")
        if self._h264_probe_clients:
            active_pipelines.append("h264-test")
        self._stats.set_runtime_state(
            capture_active=self._consumers_unlocked() and self._backend is not None,
            websocket_clients=self._websocket_clients,
            mjpeg_clients=self._mjpeg_clients,
            active_pipelines=active_pipelines,
            active_backend=self._backend.name if self._backend else None,
            backend_reason=self._backend_reason,
        )
        snapshot = self._stats._snapshot
        snapshot.h264_encoder = self._h264_settings.encoder
        snapshot.h264_bitrate_kbps = self._h264_settings.bitrate_kbps
        snapshot.output_width = self._h264_settings.width
        snapshot.output_height = self._h264_settings.height
        snapshot.h264_available_encoders = self._available_h264_encoders


def _encode_jpeg(frame: CapturedFrame) -> bytes:
    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb_bytes)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()
