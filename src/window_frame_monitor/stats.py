from __future__ import annotations

from collections import deque
from time import perf_counter_ns
from typing import Callable

from window_frame_monitor.models import BackendName, RuntimeStats


class StatsTracker:
    def __init__(self, now_ns: Callable[[], int] = perf_counter_ns) -> None:
        self._now_ns = now_ns
        self._started_ns = now_ns()
        self._frame_times_ms: deque[float] = deque(maxlen=120)
        self._frame_timestamps_ns: deque[int] = deque(maxlen=120)
        self._frame_reuse_events: deque[tuple[int, bool]] = deque(maxlen=600)
        self._last_frame_id = 0
        self._dropped_frames = 0
        self._new_frame_count = 0
        self._reused_frame_count = 0
        self._active_elapsed_ns = 0
        self._active_started_ns: int | None = None
        self._snapshot = RuntimeStats(started_ns=self._started_ns)

    def mark_frame(
        self,
        frame_id: int,
        frame_started_ns: int,
        frame_finished_ns: int,
        capture_ms: float = 0.0,
        encode_ms: float = 0.0,
        serialize_ms: float = 0.0,
        send_ms: float = 0.0,
        reused_frame: bool = False,
    ) -> None:
        if self._last_frame_id and frame_id > self._last_frame_id + 1:
            self._dropped_frames += frame_id - self._last_frame_id - 1
        self._last_frame_id = frame_id

        frame_time_ms = (frame_finished_ns - frame_started_ns) / 1_000_000
        if reused_frame:
            self._reused_frame_count += 1
        else:
            self._new_frame_count += 1
        self._frame_times_ms.append(frame_time_ms)
        self._frame_timestamps_ns.append(frame_finished_ns)
        self._frame_reuse_events.append((frame_finished_ns, reused_frame))

        runtime_s = self._active_runtime_s()
        avg = sum(self._frame_times_ms) / len(self._frame_times_ms)

        self._snapshot.frame_id = frame_id
        self._snapshot.frame_time_ms = frame_time_ms
        self._snapshot.capture_ms = capture_ms
        self._snapshot.encode_ms = encode_ms
        self._snapshot.serialize_ms = serialize_ms
        self._snapshot.send_ms = send_ms
        self._snapshot.avg_frame_time_ms = avg
        self._snapshot.max_frame_time_ms = max(self._frame_times_ms)
        self._snapshot.runtime_s = runtime_s
        self._snapshot.fps = self._calculate_fps()
        self._snapshot.dropped_frames = self._dropped_frames
        self._snapshot.new_frame_count = self._new_frame_count
        self._snapshot.reused_frame_count = self._reused_frame_count
        self._update_frame_rates(now_ns=self._now_ns())

    def set_runtime_state(
        self,
        *,
        capture_active: bool,
        websocket_clients: int,
        mjpeg_clients: int,
        active_pipelines: list[str],
        active_backend: BackendName | None,
        backend_reason: str | None,
    ) -> None:
        self._set_capture_active(capture_active)
        self._snapshot.capture_active = capture_active
        self._snapshot.websocket_clients = websocket_clients
        self._snapshot.mjpeg_clients = mjpeg_clients
        self._snapshot.active_pipelines = active_pipelines
        self._snapshot.active_backend = active_backend
        self._snapshot.backend_reason = backend_reason
        self._snapshot.runtime_s = self._active_runtime_s()

    def mark_output(self, *, serialize_ms: float = 0.0, send_ms: float = 0.0) -> None:
        self._snapshot.serialize_ms = serialize_ms
        self._snapshot.send_ms = send_ms

    def snapshot(self) -> RuntimeStats:
        self._snapshot.runtime_s = self._active_runtime_s()
        self._update_frame_rates(now_ns=self._now_ns())
        return RuntimeStats(**self._snapshot.__dict__)

    def _calculate_fps(self) -> float:
        if len(self._frame_timestamps_ns) < 2:
            return 0.0
        elapsed_s = (self._frame_timestamps_ns[-1] - self._frame_timestamps_ns[0]) / 1_000_000_000
        if elapsed_s <= 0:
            return 0.0
        return (len(self._frame_timestamps_ns) - 1) / elapsed_s

    def set_target_fps(self, target_fps: int) -> None:
        self._snapshot.target_fps = target_fps

    def _set_capture_active(self, capture_active: bool) -> None:
        now_ns = self._now_ns()
        if capture_active and self._active_started_ns is None:
            self._active_started_ns = now_ns
        elif not capture_active and self._active_started_ns is not None:
            self._active_elapsed_ns += now_ns - self._active_started_ns
            self._active_started_ns = None

    def _active_runtime_s(self) -> float:
        total_ns = self._active_elapsed_ns
        if self._active_started_ns is not None:
            total_ns += self._now_ns() - self._active_started_ns
        return max(0.0, total_ns / 1_000_000_000)

    def _update_frame_rates(self, now_ns: int) -> None:
        cutoff_ns = now_ns - 1_000_000_000
        new_frames = 0
        reused_frames = 0
        for timestamp_ns, reused in self._frame_reuse_events:
            if timestamp_ns < cutoff_ns:
                continue
            if reused:
                reused_frames += 1
            else:
                new_frames += 1
        self._snapshot.new_frames_per_s = new_frames
        self._snapshot.reused_frames_per_s = reused_frames
