from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Literal


BackendName = Literal["nvfbc", "dxgi", "windows", "test-pattern"]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int | None = None
    process_name: str | None = None
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    visible: bool = True
    minimized: bool = False


@dataclass(frozen=True)
class CapturedFrame:
    frame_id: int
    timestamp_ns: int
    width: int
    height: int
    rgb_bytes: bytes
    backend: BackendName
    reused: bool = False


@dataclass(frozen=True)
class BackendStatus:
    name: BackendName
    available: bool
    reason: str | None = None


@dataclass
class RuntimeStats:
    started_ns: int = field(default_factory=perf_counter_ns)
    frame_id: int = 0
    fps: float = 0.0
    frame_time_ms: float = 0.0
    capture_ms: float = 0.0
    encode_ms: float = 0.0
    serialize_ms: float = 0.0
    send_ms: float = 0.0
    avg_frame_time_ms: float = 0.0
    max_frame_time_ms: float = 0.0
    runtime_s: float = 0.0
    dropped_frames: int = 0
    new_frame_count: int = 0
    reused_frame_count: int = 0
    new_frames_per_s: int = 0
    reused_frames_per_s: int = 0
    target_fps: int = 30
    video_codec: str = "jpeg"
    h264_encoder: str = "h264_nvenc"
    h264_bitrate_kbps: int = 6000
    output_width: int = 1280
    output_height: int = 720
    h264_available_encoders: list[str] = field(default_factory=list)
    capture_active: bool = False
    websocket_clients: int = 0
    mjpeg_clients: int = 0
    active_pipelines: list[str] = field(default_factory=list)
    active_backend: BackendName | None = None
    backend_reason: str | None = None
