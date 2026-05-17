# Window Frame Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python local web monitor that captures a selected Windows window, reports frame statistics, and exposes WebSocket and MJPEG streams with demand-driven pipeline activation.

**Architecture:** The app uses a narrow capture backend interface, a `FrameHub` for shared frame/stat state, and a Python standard-library local server for the browser monitor plus external APIs. NVIDIA capture is represented as the preferred backend and must report clear unavailability until a local SDK binding is provided; Windows and test backends keep the prototype runnable.

**Tech Stack:** Python 3.11+, standard-library HTTP/WebSocket handling, Pillow, pytest, optional pywin32 for Windows capture helpers.

---

## File Structure

- Create: `pyproject.toml` for project metadata, dependencies, and test config.
- Create: `README.md` with run instructions and backend notes.
- Create: `src/window_frame_monitor/__init__.py` for package marker.
- Create: `src/window_frame_monitor/models.py` for shared dataclasses.
- Create: `src/window_frame_monitor/backends/base.py` for backend protocol.
- Create: `src/window_frame_monitor/backends/test_pattern.py` for deterministic synthetic frames.
- Create: `src/window_frame_monitor/backends/nvidia.py` for graceful NVIDIA capability stub.
- Create: `src/window_frame_monitor/backends/windows.py` for practical Windows fallback wrapper.
- Create: `src/window_frame_monitor/windows.py` for window listing and target resolution.
- Create: `src/window_frame_monitor/stats.py` for runtime and FPS statistics.
- Create: `src/window_frame_monitor/frame_hub.py` for capture lifecycle and stream fanout state.
- Create: `src/window_frame_monitor/server.py` for local HTTP routes, MJPEG, and WebSocket endpoint.
- Create: `src/window_frame_monitor/static/monitor.html` for browser UI.
- Create: `src/window_frame_monitor/static/monitor.js` for source switching and stats display.
- Create: `src/window_frame_monitor/static/styles.css` for monitor styling.
- Create: `src/window_frame_monitor/cli.py` for launching the local server.
- Create: `tests/test_stats.py`.
- Create: `tests/test_test_pattern_backend.py`.
- Create: `tests/test_frame_hub.py`.
- Create: `tests/test_server.py`.

---

### Task 1: Project Skeleton And Dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/window_frame_monitor/__init__.py`

- [ ] **Step 1: Create project metadata**

Write `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "window-frame-monitor"
version = "0.1.0"
description = "Local browser monitor for demand-driven window frame streaming"
requires-python = ">=3.11"
dependencies = [
  "pillow>=10.0",
  "psutil>=5.9",
]

[project.optional-dependencies]
windows = [
  "pywin32>=306; platform_system == 'Windows'",
  "mss>=9.0; platform_system == 'Windows'",
]
dev = [
  "pytest>=8.0",
]

[project.scripts]
window-frame-monitor = "window_frame_monitor.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
window_frame_monitor = ["static/*.html", "static/*.js", "static/*.css"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Add package marker**

Write `src/window_frame_monitor/__init__.py`:

```python
"""Window frame monitor package."""
```

- [ ] **Step 3: Add README**

Write `README.md`:

```markdown
# Window Frame Monitor

Python local web monitor for selected-window frame capture.

The first version exposes:

- browser monitor at `http://127.0.0.1:8765/`
- WebSocket frame stream at `/ws/frames`
- MJPEG stream at `/stream.mjpg`
- stats at `/api/stats`
- window list at `/api/windows`

NVIDIA capture is the preferred backend, but this prototype reports NVIDIA unavailability clearly unless a local SDK binding is added. Windows fallback and test-pattern modes keep the monitor and APIs runnable.

## Run

```powershell
python -m pip install -e ".[dev,windows]"
window-frame-monitor --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.
```

- [ ] **Step 4: Verify metadata**

Run: `python -m pip install -e ".[dev]"`

Expected: package installs, or network/package-index failure is reported. If dependency installation fails due to network restrictions, request approval to rerun with network access.

---

### Task 2: Shared Models And Backend Interface

**Files:**
- Create: `src/window_frame_monitor/models.py`
- Create: `src/window_frame_monitor/backends/base.py`

- [ ] **Step 1: Write model definitions**

Write `src/window_frame_monitor/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Literal


BackendName = Literal["nvidia", "windows", "test-pattern"]


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
    avg_frame_time_ms: float = 0.0
    max_frame_time_ms: float = 0.0
    runtime_s: float = 0.0
    dropped_frames: int = 0
    capture_active: bool = False
    websocket_clients: int = 0
    mjpeg_clients: int = 0
    active_pipelines: list[str] = field(default_factory=list)
    active_backend: BackendName | None = None
    backend_reason: str | None = None
```

- [ ] **Step 2: Write backend protocol**

Write `src/window_frame_monitor/backends/base.py`:

```python
from __future__ import annotations

from typing import Protocol

from window_frame_monitor.models import BackendName, CapturedFrame, WindowInfo


class CaptureBackend(Protocol):
    name: BackendName

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability and an optional human-readable unavailable reason."""

    def start(self, window: WindowInfo) -> None:
        """Start capturing the selected window."""

    def get_frame(self) -> CapturedFrame:
        """Return the next captured frame."""

    def stop(self) -> None:
        """Stop capturing and release resources."""
```

- [ ] **Step 3: Run import check**

Run: `python -c "from window_frame_monitor.models import WindowInfo; print(WindowInfo(hwnd=1, title='x'))"`

Expected: prints a `WindowInfo` representation.

---

### Task 3: Statistics Engine

**Files:**
- Create: `src/window_frame_monitor/stats.py`
- Test: `tests/test_stats.py`

- [ ] **Step 1: Write failing tests**

Write `tests/test_stats.py`:

```python
from window_frame_monitor.stats import StatsTracker


def test_stats_tracker_records_runtime_and_frame_times():
    tracker = StatsTracker(now_ns=lambda: 0)

    tracker.mark_frame(frame_id=1, frame_started_ns=0, frame_finished_ns=10_000_000)
    snapshot = tracker.snapshot()

    assert snapshot.frame_id == 1
    assert snapshot.frame_time_ms == 10.0
    assert snapshot.avg_frame_time_ms == 10.0
    assert snapshot.max_frame_time_ms == 10.0


def test_stats_tracker_counts_dropped_frames():
    tracker = StatsTracker(now_ns=lambda: 50_000_000)

    tracker.mark_frame(frame_id=1, frame_started_ns=0, frame_finished_ns=10_000_000)
    tracker.mark_frame(frame_id=3, frame_started_ns=20_000_000, frame_finished_ns=30_000_000)
    snapshot = tracker.snapshot()

    assert snapshot.dropped_frames == 1
    assert snapshot.frame_id == 3
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_stats.py -v`

Expected: FAIL because `window_frame_monitor.stats` does not exist.

- [ ] **Step 3: Implement stats tracker**

Write `src/window_frame_monitor/stats.py`:

```python
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
        self._last_frame_id = 0
        self._dropped_frames = 0
        self._snapshot = RuntimeStats(started_ns=self._started_ns)

    def mark_frame(self, frame_id: int, frame_started_ns: int, frame_finished_ns: int) -> None:
        if self._last_frame_id and frame_id > self._last_frame_id + 1:
            self._dropped_frames += frame_id - self._last_frame_id - 1
        self._last_frame_id = frame_id

        frame_time_ms = (frame_finished_ns - frame_started_ns) / 1_000_000
        self._frame_times_ms.append(frame_time_ms)
        self._frame_timestamps_ns.append(frame_finished_ns)

        runtime_s = max(0.0, (self._now_ns() - self._started_ns) / 1_000_000_000)
        fps = self._calculate_fps()
        avg = sum(self._frame_times_ms) / len(self._frame_times_ms)

        self._snapshot.frame_id = frame_id
        self._snapshot.frame_time_ms = frame_time_ms
        self._snapshot.avg_frame_time_ms = avg
        self._snapshot.max_frame_time_ms = max(self._frame_times_ms)
        self._snapshot.runtime_s = runtime_s
        self._snapshot.fps = fps
        self._snapshot.dropped_frames = self._dropped_frames

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
        self._snapshot.capture_active = capture_active
        self._snapshot.websocket_clients = websocket_clients
        self._snapshot.mjpeg_clients = mjpeg_clients
        self._snapshot.active_pipelines = active_pipelines
        self._snapshot.active_backend = active_backend
        self._snapshot.backend_reason = backend_reason
        self._snapshot.runtime_s = max(0.0, (self._now_ns() - self._started_ns) / 1_000_000_000)

    def snapshot(self) -> RuntimeStats:
        return RuntimeStats(**self._snapshot.__dict__)

    def _calculate_fps(self) -> float:
        if len(self._frame_timestamps_ns) < 2:
            return 0.0
        elapsed_s = (self._frame_timestamps_ns[-1] - self._frame_timestamps_ns[0]) / 1_000_000_000
        if elapsed_s <= 0:
            return 0.0
        return (len(self._frame_timestamps_ns) - 1) / elapsed_s
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_stats.py -v`

Expected: PASS.

---

### Task 4: Test Pattern And NVIDIA Stub Backends

**Files:**
- Create: `src/window_frame_monitor/backends/test_pattern.py`
- Create: `src/window_frame_monitor/backends/nvidia.py`
- Test: `tests/test_test_pattern_backend.py`

- [ ] **Step 1: Write backend tests**

Write `tests/test_test_pattern_backend.py`:

```python
from window_frame_monitor.backends.nvidia import NvidiaWindowCaptureBackend
from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.models import WindowInfo


def test_test_pattern_backend_emits_changing_frames():
    backend = TestPatternBackend(width=64, height=36)
    backend.start(WindowInfo(hwnd=0, title="test"))

    first = backend.get_frame()
    second = backend.get_frame()

    assert first.width == 64
    assert first.height == 36
    assert first.rgb_bytes != second.rgb_bytes
    assert second.frame_id == first.frame_id + 1


def test_nvidia_backend_reports_unavailable_without_binding():
    backend = NvidiaWindowCaptureBackend()

    available, reason = backend.is_available()

    assert available is False
    assert reason
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_test_pattern_backend.py -v`

Expected: FAIL because backend modules do not exist.

- [ ] **Step 3: Implement test pattern backend**

Write `src/window_frame_monitor/backends/test_pattern.py`:

```python
from __future__ import annotations

from time import perf_counter_ns

from PIL import Image, ImageDraw

from window_frame_monitor.models import CapturedFrame, WindowInfo


class TestPatternBackend:
    name = "test-pattern"

    def __init__(self, width: int = 640, height: int = 360) -> None:
        self._width = width
        self._height = height
        self._frame_id = 0
        self._window: WindowInfo | None = None

    def is_available(self) -> tuple[bool, str | None]:
        return True, None

    def start(self, window: WindowInfo) -> None:
        self._window = window
        self._frame_id = 0

    def get_frame(self) -> CapturedFrame:
        if self._window is None:
            raise RuntimeError("TestPatternBackend.start() must be called before get_frame().")

        self._frame_id += 1
        image = Image.new("RGB", (self._width, self._height), (18, 22, 30))
        draw = ImageDraw.Draw(image)
        bar_x = (self._frame_id * 7) % self._width
        draw.rectangle((bar_x, 0, min(self._width, bar_x + 60), self._height), fill=(40, 150, 220))
        draw.text((16, 16), f"test frame {self._frame_id}", fill=(245, 245, 245))
        return CapturedFrame(
            frame_id=self._frame_id,
            timestamp_ns=perf_counter_ns(),
            width=self._width,
            height=self._height,
            rgb_bytes=image.tobytes(),
            backend=self.name,
        )

    def stop(self) -> None:
        self._window = None
```

- [ ] **Step 4: Implement NVIDIA capability stub**

Write `src/window_frame_monitor/backends/nvidia.py`:

```python
from __future__ import annotations

from window_frame_monitor.models import CapturedFrame, WindowInfo


class NvidiaWindowCaptureBackend:
    name = "nvidia"

    def __init__(self) -> None:
        self._reason = (
            "NVIDIA Capture SDK binding is not configured. "
            "Install or implement a local NVFBC/Capture SDK binding to enable this backend."
        )

    def is_available(self) -> tuple[bool, str | None]:
        return False, self._reason

    def start(self, window: WindowInfo) -> None:
        raise RuntimeError(self._reason)

    def get_frame(self) -> CapturedFrame:
        raise RuntimeError(self._reason)

    def stop(self) -> None:
        return None
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_test_pattern_backend.py -v`

Expected: PASS.

---

### Task 5: Window Listing And Windows Fallback Backend

**Files:**
- Create: `src/window_frame_monitor/windows.py`
- Create: `src/window_frame_monitor/backends/windows.py`

- [ ] **Step 1: Implement window selector**

Write `src/window_frame_monitor/windows.py`:

```python
from __future__ import annotations

import sys

import psutil

from window_frame_monitor.models import WindowInfo


def list_windows() -> list[WindowInfo]:
    if sys.platform != "win32":
        return [WindowInfo(hwnd=0, title="Test Pattern", process_name="test-pattern")]

    import win32gui
    import win32process

    windows: list[WindowInfo] = []

    def visit(hwnd: int, _extra: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = None
        try:
            process_name = psutil.Process(pid).name()
        except psutil.Error:
            process_name = None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        windows.append(
            WindowInfo(
                hwnd=hwnd,
                title=title,
                pid=pid,
                process_name=process_name,
                x=left,
                y=top,
                width=max(0, right - left),
                height=max(0, bottom - top),
                visible=True,
                minimized=bool(win32gui.IsIconic(hwnd)),
            )
        )

    win32gui.EnumWindows(visit, None)
    return windows


def resolve_window(*, hwnd: int | None = None, title: str | None = None, process_name: str | None = None) -> WindowInfo:
    candidates = list_windows()
    if hwnd is not None:
        matches = [window for window in candidates if window.hwnd == hwnd]
    elif title:
        lowered = title.lower()
        matches = [window for window in candidates if lowered in window.title.lower()]
    elif process_name:
        lowered = process_name.lower()
        matches = [window for window in candidates if window.process_name and lowered in window.process_name.lower()]
    else:
        raise ValueError("Provide hwnd, title, or process_name.")

    if not matches:
        raise ValueError("No matching window found.")
    if len(matches) > 1:
        names = ", ".join(f"{window.hwnd}:{window.title}" for window in matches[:5])
        raise ValueError(f"Multiple matching windows found: {names}")
    return matches[0]
```

- [ ] **Step 2: Implement Windows fallback backend**

Write `src/window_frame_monitor/backends/windows.py`:

```python
from __future__ import annotations

import sys
from time import perf_counter_ns

from PIL import ImageGrab

from window_frame_monitor.models import CapturedFrame, WindowInfo


class WindowsWindowCaptureBackend:
    name = "windows"

    def __init__(self) -> None:
        self._window: WindowInfo | None = None
        self._frame_id = 0

    def is_available(self) -> tuple[bool, str | None]:
        if sys.platform != "win32":
            return False, "Windows capture backend is only available on Windows."
        return True, None

    def start(self, window: WindowInfo) -> None:
        if window.minimized:
            raise RuntimeError("Cannot capture a minimized window.")
        if window.width <= 0 or window.height <= 0:
            raise RuntimeError("Cannot capture a window with empty bounds.")
        self._window = window
        self._frame_id = 0

    def get_frame(self) -> CapturedFrame:
        if self._window is None:
            raise RuntimeError("WindowsWindowCaptureBackend.start() must be called before get_frame().")

        self._frame_id += 1
        box = (
            self._window.x,
            self._window.y,
            self._window.x + self._window.width,
            self._window.y + self._window.height,
        )
        image = ImageGrab.grab(bbox=box).convert("RGB")
        return CapturedFrame(
            frame_id=self._frame_id,
            timestamp_ns=perf_counter_ns(),
            width=image.width,
            height=image.height,
            rgb_bytes=image.tobytes(),
            backend=self.name,
        )

    def stop(self) -> None:
        self._window = None
```

- [ ] **Step 3: Run import check**

Run: `python -c "from window_frame_monitor.windows import list_windows; print(len(list_windows()))"`

Expected: prints a count. On non-Windows, it prints `1` for the test-pattern entry.

---

### Task 6: FrameHub Lifecycle And Demand Activation

**Files:**
- Create: `src/window_frame_monitor/frame_hub.py`
- Test: `tests/test_frame_hub.py`

- [ ] **Step 1: Write FrameHub tests**

Write `tests/test_frame_hub.py`:

```python
import asyncio

import pytest

from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.frame_hub import FrameHub
from window_frame_monitor.models import WindowInfo


@pytest.mark.asyncio
async def test_frame_hub_idles_without_consumers():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await asyncio.sleep(0.05)
    stats = hub.snapshot_stats()

    assert stats.capture_active is False
    assert stats.frame_id == 0


@pytest.mark.asyncio
async def test_frame_hub_captures_with_websocket_consumer():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await hub.add_websocket_client()
    await asyncio.sleep(0.12)
    frame = await hub.latest_jpeg()
    await hub.remove_websocket_client()

    assert frame is not None
    assert hub.snapshot_stats().websocket_clients == 0
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_frame_hub.py -v`

Expected: FAIL because `FrameHub` does not exist.

- [ ] **Step 3: Implement FrameHub**

Write `src/window_frame_monitor/frame_hub.py`:

```python
from __future__ import annotations

import asyncio
from dataclasses import asdict
from io import BytesIO
from time import perf_counter_ns

from PIL import Image

from window_frame_monitor.backends.base import CaptureBackend
from window_frame_monitor.models import CapturedFrame, RuntimeStats, WindowInfo
from window_frame_monitor.stats import StatsTracker


class FrameHub:
    def __init__(self, backends: list[CaptureBackend], target_fps: int = 30) -> None:
        self._backends = backends
        self._target_fps = target_fps
        self._target: WindowInfo | None = None
        self._backend: CaptureBackend | None = None
        self._backend_reason: str | None = None
        self._websocket_clients = 0
        self._mjpeg_clients = 0
        self._stats = StatsTracker()
        self._latest_frame: CapturedFrame | None = None
        self._latest_jpeg: bytes | None = None
        self._capture_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._frame_event = asyncio.Event()
        self._stopping = False

    async def set_target(self, window: WindowInfo) -> None:
        async with self._lock:
            self._target = window
            self._select_backend_locked()
            if self._backend and self._consumers_locked():
                self._ensure_task_locked()
            self._refresh_stats_locked()

    async def add_websocket_client(self) -> None:
        async with self._lock:
            self._websocket_clients += 1
            self._ensure_task_locked()
            self._refresh_stats_locked()

    async def remove_websocket_client(self) -> None:
        async with self._lock:
            self._websocket_clients = max(0, self._websocket_clients - 1)
            self._refresh_stats_locked()

    async def add_mjpeg_client(self) -> None:
        async with self._lock:
            self._mjpeg_clients += 1
            self._ensure_task_locked()
            self._refresh_stats_locked()

    async def remove_mjpeg_client(self) -> None:
        async with self._lock:
            self._mjpeg_clients = max(0, self._mjpeg_clients - 1)
            self._refresh_stats_locked()

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

    def snapshot_stats(self) -> RuntimeStats:
        self._refresh_stats_unlocked()
        return self._stats.snapshot()

    def stats_dict(self) -> dict[str, object]:
        return asdict(self.snapshot_stats())

    async def close(self) -> None:
        self._stopping = True
        task = self._capture_task
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._backend:
            self._backend.stop()

    def _select_backend_locked(self) -> None:
        self._backend = None
        self._backend_reason = None
        for backend in self._backends:
            available, reason = backend.is_available()
            if available:
                self._backend = backend
                self._backend_reason = None
                return
            if self._backend_reason is None:
                self._backend_reason = reason

    def _ensure_task_locked(self) -> None:
        if self._target is None:
            return
        if self._backend is None:
            self._select_backend_locked()
        if self._backend is None:
            return
        if self._capture_task is None or self._capture_task.done():
            self._capture_task = asyncio.create_task(self._capture_loop())

    async def _capture_loop(self) -> None:
        assert self._backend is not None
        assert self._target is not None
        self._backend.start(self._target)
        interval_s = 1 / max(1, self._target_fps)
        try:
            while not self._stopping:
                if not self._consumers_unlocked():
                    await asyncio.sleep(0.05)
                    self._refresh_stats_unlocked()
                    continue
                started_ns = perf_counter_ns()
                frame = await asyncio.to_thread(self._backend.get_frame)
                jpeg = await asyncio.to_thread(_encode_jpeg, frame)
                finished_ns = perf_counter_ns()
                self._latest_frame = frame
                self._latest_jpeg = jpeg
                self._stats.mark_frame(frame.frame_id, started_ns, finished_ns)
                self._refresh_stats_unlocked()
                self._frame_event.set()
                await asyncio.sleep(interval_s)
        finally:
            self._backend.stop()

    def _consumers_locked(self) -> bool:
        return self._websocket_clients > 0 or self._mjpeg_clients > 0

    def _consumers_unlocked(self) -> bool:
        return self._websocket_clients > 0 or self._mjpeg_clients > 0

    def _refresh_stats_locked(self) -> None:
        self._refresh_stats_unlocked()

    def _refresh_stats_unlocked(self) -> None:
        active_pipelines = []
        if self._websocket_clients:
            active_pipelines.append("websocket")
        if self._mjpeg_clients:
            active_pipelines.append("mjpeg")
        self._stats.set_runtime_state(
            capture_active=self._consumers_unlocked() and self._backend is not None,
            websocket_clients=self._websocket_clients,
            mjpeg_clients=self._mjpeg_clients,
            active_pipelines=active_pipelines,
            active_backend=self._backend.name if self._backend else None,
            backend_reason=self._backend_reason,
        )


def _encode_jpeg(frame: CapturedFrame) -> bytes:
    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb_bytes)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_frame_hub.py -v`

Expected: PASS.

---

### Task 7: FastAPI Server And Streams

**Files:**
- Create: `src/window_frame_monitor/server.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write server tests**

Write `tests/test_server.py`:

```python
from fastapi.testclient import TestClient

from window_frame_monitor.server import create_app


def test_stats_endpoint_returns_runtime_state():
    app = create_app(use_test_backend=True)
    client = TestClient(app)

    response = client.get("/api/stats")

    assert response.status_code == 200
    body = response.json()
    assert "capture_active" in body
    assert "websocket_clients" in body
    assert "mjpeg_clients" in body


def test_windows_endpoint_returns_list():
    app = create_app(use_test_backend=True)
    client = TestClient(app)

    response = client.get("/api/windows")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_server.py -v`

Expected: FAIL because `server.py` does not exist.

- [ ] **Step 3: Implement FastAPI app**

Write `src/window_frame_monitor/server.py`:

```python
from __future__ import annotations

import base64
from dataclasses import asdict
from importlib.resources import files
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from window_frame_monitor.backends.nvidia import NvidiaWindowCaptureBackend
from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.backends.windows import WindowsWindowCaptureBackend
from window_frame_monitor.frame_hub import FrameHub
from window_frame_monitor.models import WindowInfo
from window_frame_monitor.windows import list_windows, resolve_window


class TargetRequest(BaseModel):
    hwnd: int | None = None
    title: str | None = None
    process_name: str | None = None


def create_app(use_test_backend: bool = False) -> FastAPI:
    backends = [NvidiaWindowCaptureBackend(), WindowsWindowCaptureBackend()]
    if use_test_backend:
        backends = [TestPatternBackend()]
    else:
        backends.append(TestPatternBackend())

    hub = FrameHub(backends=backends)
    app = FastAPI(title="Window Frame Monitor")
    app.state.frame_hub = hub

    static_dir = files("window_frame_monitor").joinpath("static")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await hub.close()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = static_dir.joinpath("monitor.html").read_text(encoding="utf-8")
        return HTMLResponse(html)

    @app.get("/api/windows")
    async def api_windows() -> list[dict[str, object]]:
        return [asdict(window) for window in list_windows()]

    @app.post("/api/target")
    async def api_target(request: TargetRequest) -> dict[str, object]:
        try:
            window = resolve_window(hwnd=request.hwnd, title=request.title, process_name=request.process_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await hub.set_target(window)
        return {"target": asdict(window), "stats": hub.stats_dict()}

    @app.get("/api/stats")
    async def api_stats() -> dict[str, object]:
        return hub.stats_dict()

    @app.get("/stream.mjpg")
    async def stream_mjpg() -> StreamingResponse:
        return StreamingResponse(_mjpeg_generator(hub), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/ws/frames")
    async def ws_frames(websocket: WebSocket) -> None:
        await websocket.accept()
        await hub.add_websocket_client()
        last_frame_id = 0
        try:
            while True:
                result = await hub.wait_for_next_jpeg(after_frame_id=last_frame_id)
                if result is None:
                    await websocket.send_json({"type": "heartbeat", "stats": hub.stats_dict()})
                    continue
                last_frame_id, jpeg = result
                stats = hub.stats_dict()
                await websocket.send_json(
                    {
                        "type": "frame",
                        "frame_id": last_frame_id,
                        "image": {
                            "format": "jpeg",
                            "data_base64": base64.b64encode(jpeg).decode("ascii"),
                        },
                        "stats": stats,
                    }
                )
        except WebSocketDisconnect:
            pass
        finally:
            await hub.remove_websocket_client()

    return app


async def _mjpeg_generator(hub: FrameHub) -> AsyncIterator[bytes]:
    await hub.add_mjpeg_client()
    last_frame_id = 0
    try:
        while True:
            result = await hub.wait_for_next_jpeg(after_frame_id=last_frame_id)
            if result is None:
                continue
            last_frame_id, jpeg = result
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
    finally:
        await hub.remove_mjpeg_client()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_server.py -v`

Expected: PASS.

---

### Task 8: Browser Monitor UI

**Files:**
- Create: `src/window_frame_monitor/static/monitor.html`
- Create: `src/window_frame_monitor/static/monitor.js`
- Create: `src/window_frame_monitor/static/styles.css`

- [ ] **Step 1: Add monitor HTML**

Write `src/window_frame_monitor/static/monitor.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Window Frame Monitor</title>
    <link rel="stylesheet" href="/static/styles.css" />
  </head>
  <body>
    <main class="shell">
      <header class="topbar">
        <div>
          <h1>Window Frame Monitor</h1>
          <p id="backend-status">Backend waiting</p>
        </div>
        <div class="source-toggle" role="group" aria-label="Monitor source">
          <button id="source-ws" class="active" type="button">WebSocket</button>
          <button id="source-mjpeg" type="button">MJPEG</button>
        </div>
      </header>

      <section class="controls">
        <select id="window-select" aria-label="Target window"></select>
        <button id="refresh-windows" type="button">Refresh</button>
        <button id="select-window" type="button">Capture</button>
      </section>

      <section class="preview" aria-label="Live preview">
        <img id="preview-image" alt="Live captured frame" />
      </section>

      <section class="metrics" aria-label="Runtime metrics">
        <div><span>Runtime</span><strong id="runtime">0.0s</strong></div>
        <div><span>FPS</span><strong id="fps">0.0</strong></div>
        <div><span>Frame</span><strong id="frame-time">0.0 ms</strong></div>
        <div><span>Average</span><strong id="avg-frame-time">0.0 ms</strong></div>
        <div><span>Maximum</span><strong id="max-frame-time">0.0 ms</strong></div>
        <div><span>Dropped</span><strong id="dropped">0</strong></div>
        <div><span>WS clients</span><strong id="ws-clients">0</strong></div>
        <div><span>MJPEG clients</span><strong id="mjpeg-clients">0</strong></div>
        <div><span>Capture</span><strong id="capture-state">idle</strong></div>
        <div><span>Pipelines</span><strong id="pipelines">none</strong></div>
      </section>
    </main>
    <script src="/static/monitor.js"></script>
  </body>
</html>
```

- [ ] **Step 2: Add monitor styles**

Write `src/window_frame_monitor/static/styles.css`:

```css
:root {
  color-scheme: dark;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #101216;
  color: #f4f7fb;
}

body {
  margin: 0;
  background: #101216;
}

.shell {
  min-height: 100vh;
  display: grid;
  grid-template-rows: auto auto minmax(240px, 1fr) auto;
}

.topbar,
.controls,
.metrics {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid #2a2f38;
}

.topbar {
  justify-content: space-between;
}

h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
}

p {
  margin: 4px 0 0;
  color: #aeb7c6;
}

button,
select {
  background: #1c222b;
  color: #f4f7fb;
  border: 1px solid #343c49;
  border-radius: 6px;
  min-height: 36px;
  padding: 0 12px;
}

button.active {
  background: #2f6feb;
  border-color: #4f8cff;
}

select {
  min-width: min(680px, 70vw);
}

.preview {
  display: grid;
  place-items: center;
  background: #07090d;
  overflow: hidden;
}

.preview img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  border-top: 1px solid #2a2f38;
  border-bottom: 0;
}

.metrics div {
  display: grid;
  gap: 4px;
}

.metrics span {
  color: #aeb7c6;
  font-size: 12px;
}

.metrics strong {
  font-size: 18px;
  font-weight: 650;
}
```

- [ ] **Step 3: Add monitor JavaScript**

Write `src/window_frame_monitor/static/monitor.js`:

```javascript
const preview = document.querySelector("#preview-image");
const windowSelect = document.querySelector("#window-select");
const backendStatus = document.querySelector("#backend-status");
const wsButton = document.querySelector("#source-ws");
const mjpegButton = document.querySelector("#source-mjpeg");

let socket = null;
let mode = "websocket";

function setText(id, value) {
  document.querySelector(id).textContent = value;
}

function applyStats(stats) {
  backendStatus.textContent = `Backend: ${stats.active_backend || "waiting"}${stats.backend_reason ? ` (${stats.backend_reason})` : ""}`;
  setText("#runtime", `${stats.runtime_s.toFixed(1)}s`);
  setText("#fps", stats.fps.toFixed(1));
  setText("#frame-time", `${stats.frame_time_ms.toFixed(1)} ms`);
  setText("#avg-frame-time", `${stats.avg_frame_time_ms.toFixed(1)} ms`);
  setText("#max-frame-time", `${stats.max_frame_time_ms.toFixed(1)} ms`);
  setText("#dropped", stats.dropped_frames);
  setText("#ws-clients", stats.websocket_clients);
  setText("#mjpeg-clients", stats.mjpeg_clients);
  setText("#capture-state", stats.capture_active ? "active" : "idle");
  setText("#pipelines", stats.active_pipelines.length ? stats.active_pipelines.join(", ") : "none");
}

async function loadWindows() {
  const response = await fetch("/api/windows");
  const windows = await response.json();
  windowSelect.innerHTML = "";
  for (const win of windows) {
    const option = document.createElement("option");
    option.value = String(win.hwnd);
    option.textContent = `${win.title} ${win.process_name ? `(${win.process_name})` : ""}`;
    windowSelect.appendChild(option);
  }
}

async function selectWindow() {
  const hwnd = Number(windowSelect.value);
  const response = await fetch("/api/target", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hwnd }),
  });
  const body = await response.json();
  if (!response.ok) {
    backendStatus.textContent = body.detail || "Failed to select window";
    return;
  }
  applyStats(body.stats);
  startSelectedMode();
}

function stopStreams() {
  preview.removeAttribute("src");
  if (socket) {
    socket.close();
    socket = null;
  }
}

function startWebSocket() {
  stopStreams();
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws/frames`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.stats) {
      applyStats(message.stats);
    }
    if (message.type === "frame" && message.image?.data_base64) {
      preview.src = `data:image/jpeg;base64,${message.image.data_base64}`;
    }
  };
}

function startMjpeg() {
  stopStreams();
  preview.src = `/stream.mjpg?cacheBust=${Date.now()}`;
}

function startSelectedMode() {
  wsButton.classList.toggle("active", mode === "websocket");
  mjpegButton.classList.toggle("active", mode === "mjpeg");
  if (mode === "websocket") {
    startWebSocket();
  } else {
    startMjpeg();
  }
}

async function pollStats() {
  const response = await fetch("/api/stats");
  applyStats(await response.json());
}

document.querySelector("#refresh-windows").addEventListener("click", loadWindows);
document.querySelector("#select-window").addEventListener("click", selectWindow);
wsButton.addEventListener("click", () => {
  mode = "websocket";
  startSelectedMode();
});
mjpegButton.addEventListener("click", () => {
  mode = "mjpeg";
  startSelectedMode();
});

loadWindows();
setInterval(pollStats, 1000);
```

- [ ] **Step 4: Run static route check**

Run: `pytest tests/test_server.py -v`

Expected: PASS.

---

### Task 9: CLI Entrypoint

**Files:**
- Create: `src/window_frame_monitor/cli.py`

- [ ] **Step 1: Add CLI**

Write `src/window_frame_monitor/cli.py`:

```python
from __future__ import annotations

import argparse

import uvicorn

from window_frame_monitor.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the window frame monitor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--test-backend", action="store_true", help="Use synthetic frames instead of real capture backends.")
    args = parser.parse_args()

    app = create_app(use_test_backend=args.test_backend)
    uvicorn.run(app, host=args.host, port=args.port)
```

- [ ] **Step 2: Run CLI help**

Run: `python -m window_frame_monitor.cli --help`

Expected: displays host, port, and test-backend options.

---

### Task 10: Full Verification

**Files:**
- Modify as needed only if verification exposes bugs in previous tasks.

- [ ] **Step 1: Run full automated tests**

Run: `pytest -v`

Expected: all tests pass.

- [ ] **Step 2: Start monitor with test backend**

Run: `window-frame-monitor --host 127.0.0.1 --port 8765 --test-backend`

Expected: server starts at `http://127.0.0.1:8765/`.

- [ ] **Step 3: Manual browser verification**

Open `http://127.0.0.1:8765/`.

Expected:

- Window selector contains `Test Pattern`.
- Capturing starts after pressing `Capture`.
- WebSocket mode shows changing frames.
- Stats update once per second or faster.
- Switching to MJPEG shows the same synthetic stream.
- Active pipeline changes from `websocket` to `mjpeg`.

- [ ] **Step 4: Windows window capture verification**

On Windows with dependencies installed, run without `--test-backend`:

```powershell
window-frame-monitor --host 127.0.0.1 --port 8765
```

Expected:

- Window list shows visible desktop windows.
- NVIDIA backend reports unavailable unless a real SDK binding exists.
- Windows backend activates for a valid non-minimized window.
- Monitor shows the selected window crop.

---

## Self-Review

Spec coverage:

- Specified window capture: Task 5 and Task 7.
- NVIDIA preferred backend: Task 4 and Task 7.
- Windows fallback: Task 5 and Task 7.
- Browser monitor: Task 8.
- WebSocket primary stream: Task 7 and Task 8.
- MJPEG auxiliary stream: Task 7 and Task 8.
- Source switching: Task 8.
- Demand activation: Task 6 and Task 7.
- Stats: Task 3, Task 6, Task 8.
- Testability without NVIDIA: Task 4, Task 6, Task 10.

Placeholder scan:

- No TBD or TODO placeholders are intentionally left in this plan.

Type consistency:

- `WindowInfo`, `CapturedFrame`, `RuntimeStats`, and `CaptureBackend` names are consistent across tasks.
- `FrameHub` methods used by `server.py` are defined in Task 6.
- Static files referenced by `server.py` are created in Task 8.
