# Window Frame Monitor Design

Date: 2026-05-14

## Goal

Build a Python-based local prototype that captures frames from a specified Windows window, measures runtime and frame statistics, and exposes the stream through a browser monitor. The preferred capture path is NVIDIA Capture SDK / NVFBC, with DXGI Desktop Duplication as the preferred Windows fallback and a slower Windows screenshot path as the final fallback.

The first version should prove the full loop:

```text
select window -> capture frames -> collect stats -> expose streams -> monitor in browser
```

## Scope

Included in the first version:

- Capture a specified window, selected by title, process name, or window handle.
- Prefer an NVIDIA Capture SDK / NVFBC backend when available.
- Fall back to DXGI Desktop Duplication when NVIDIA capture is unavailable.
- Fall back to the slower Windows screenshot backend when DXGI is unavailable.
- Provide a browser-based monitor UI.
- Provide WebSocket frame streaming as the primary interface for future agents.
- Provide MJPEG HTTP streaming as an auxiliary monitoring interface.
- Let the monitor choose between WebSocket and MJPEG as its viewing source.
- Activate each output pipeline only when it is selected or has active clients.
- Track FPS, frame timing, runtime, dropped frames, active backend, selected window, and active pipelines.

Excluded from the first version:

- Full low-level C++ NVIDIA Capture SDK integration.
- GPU zero-copy tensor transport.
- Input injection or game control.
- Agent planning or model inference.
- Recording to disk.
- Multi-machine streaming.

## Architecture

The system is split into small modules with explicit boundaries.

```text
WindowSelector
        |
        v
CaptureManager
        |
        v
FrameHub
        |
        +--> WebSocketFramePipeline
        |
        +--> MjpegStreamPipeline
        |
        v
Web Monitor UI
```

### WindowSelector

Finds candidate windows and resolves the target window.

Responsibilities:

- List visible windows.
- Include title, process name, process id, handle, and bounds when available.
- Resolve a requested title, process name, or handle to one target.
- Report clear errors for missing, ambiguous, minimized, or inaccessible windows.

### CaptureManager

Owns backend selection and capture lifecycle.

Responsibilities:

- Try `NvidiaNvFbcCaptureBackend` first.
- If unavailable, store the reason and try `DxgiDesktopDuplicationBackend`.
- If DXGI is unavailable, store the reason and try `WindowsWindowCaptureBackend`.
- If both real backends are unavailable, allow `TestPatternBackend` for UI and API verification.
- Start capture only when at least one consumer needs frames.
- Stop or idle capture when there are no frame consumers.
- Publish backend status to the monitor and API.

### Capture Backends

All capture backends implement the same interface:

```python
class CaptureBackend:
    name: str

    def is_available(self) -> tuple[bool, str | None]: ...
    def start(self, window: WindowInfo) -> None: ...
    def get_frame(self) -> CapturedFrame: ...
    def stop(self) -> None: ...
```

`NvidiaNvFbcCaptureBackend` is the preferred backend. In the first version it detects NVIDIA Capture SDK / NvFBC DLL availability and reports a clear unavailable reason until a native Python binding is provided.

`DxgiDesktopDuplicationBackend` is the preferred Windows fallback. It uses the optional `dxcam` binding when installed, which wraps Desktop Duplication capture, then crops to the selected window region.

`WindowsWindowCaptureBackend` is the slow compatibility fallback backend. It uses a practical Python-accessible Windows screenshot path.

`TestPatternBackend` emits synthetic frames for development and automated checks.

### FrameHub

Coordinates the latest frame and statistics.

Responsibilities:

- Hold the newest captured frame.
- Assign monotonically increasing frame ids.
- Track capture time, encode time, end-to-end latency, FPS, average frame time, max frame time, dropped frames, and runtime.
- Maintain active client counts for WebSocket and MJPEG.
- Decide whether capture should be active, based on consumer demand.
- Share encoded JPEG data between pipelines when both need the same encoded representation.

### WebSocketFramePipeline

Primary frame stream for future agents and optional monitor viewing.

Activation rule:

- Active when at least one WebSocket client is connected.
- If the browser monitor selects WebSocket viewing, it connects as one WebSocket client.

Initial payload format uses JSON with base64 JPEG for simplicity:

```json
{
  "type": "frame",
  "frame_id": 1234,
  "timestamp_ns": 123456789,
  "backend": "dxgi",
  "window": {
    "title": "Game Window",
    "hwnd": 123456,
    "pid": 9999
  },
  "image": {
    "format": "jpeg",
    "width": 1280,
    "height": 720,
    "data_base64": "..."
  },
  "stats": {
    "fps": 58.7,
    "frame_time_ms": 4.2,
    "avg_frame_time_ms": 5.1,
    "max_frame_time_ms": 11.8,
    "runtime_s": 35.4,
    "dropped_frames": 2
  }
}
```

This format is intentionally simple. A later version can switch to binary frames plus compact metadata for lower latency.

### MjpegStreamPipeline

Auxiliary stream for quick browser monitoring.

Activation rule:

- Active only when one or more clients request `/stream.mjpg`.
- If the browser monitor selects MJPEG viewing, it opens the MJPEG stream.
- If not selected and no external client is connected, the MJPEG pipeline does not encode or stream frames.

### Web Monitor UI

The first monitor is a browser page served by the Python service.

Required UI:

- Target window selector.
- Backend status: NVFBC active, DXGI active, Windows fallback active, or test backend.
- Unavailable backend reasons.
- Source selector: WebSocket frame stream or MJPEG HTTP stream.
- Live preview.
- Runtime.
- FPS.
- Current frame time.
- Average frame time.
- Maximum frame time.
- Dropped frames.
- WebSocket client count.
- MJPEG client count.
- Capture state: active or idle.
- Active output pipelines.

The monitor defaults to WebSocket viewing because WebSocket is the primary interface for future agents.

## Data Flow

When no one needs video:

```text
no consumers -> capture idle -> no output encoding
```

When the monitor chooses WebSocket:

```text
browser WebSocket connected -> capture active -> WebSocket encoding active -> MJPEG inactive
```

When the monitor chooses MJPEG:

```text
browser MJPEG connected -> capture active -> MJPEG encoding active -> WebSocket inactive unless another client is connected
```

When an agent uses WebSocket and the monitor uses MJPEG:

```text
one capture loop -> latest frame in FrameHub -> WebSocket active + MJPEG active
```

## HTTP And WebSocket API

Initial endpoints:

- `GET /`: monitor page.
- `GET /api/windows`: list candidate windows.
- `POST /api/target`: set target window.
- `GET /api/stats`: current stats and backend state.
- `GET /stream.mjpg`: MJPEG stream.
- `WS /ws/frames`: WebSocket frame stream.

Optional later endpoints:

- `POST /api/backend`: force a backend for testing.
- `POST /api/capture/start`: manually start capture.
- `POST /api/capture/stop`: manually stop capture.

## Backend Selection

Startup behavior:

1. List windows.
2. Wait for target selection or accept a configured target.
3. Check NVIDIA Capture SDK / NVFBC backend availability.
4. If NVFBC is available, start it.
5. If NVFBC is unavailable, report the exact reason and try DXGI Desktop Duplication.
6. If DXGI is unavailable, report the exact reason and start Windows screenshot fallback.
7. If Windows fallback is unavailable, use test pattern mode only if explicitly allowed.

The UI should always show both the active backend and any fallback reason.

## Error Handling

Expected errors:

- Target window not found.
- Multiple matching windows.
- Window minimized.
- Window resized or moved.
- Window closed during capture.
- NVIDIA Capture SDK / NVFBC backend unavailable.
- DXGI binding unavailable.
- Windows fallback unavailable.
- Frame timeout.
- JPEG encode failure.
- WebSocket client disconnect.
- MJPEG client disconnect.

Behavior:

- Keep the service alive when a backend or client fails.
- Surface errors in `/api/stats` and the monitor UI.
- Try to recover if the same window becomes available again.
- Stop capture when the target window closes and no replacement target is selected.

## Testing Strategy

The first implementation should be testable even without NVIDIA capture.

Checks:

- `TestPatternBackend` emits frames with changing content.
- Stats update while test frames are emitted.
- Capture idles when no stream is active.
- WebSocket stream activates capture and receives frame messages.
- MJPEG stream activates capture and returns multipart JPEG frames.
- Switching monitor source closes the previous browser-side stream.
- Backend fallback records NVIDIA and DXGI unavailable reasons.

Manual verification:

- Start service.
- Open monitor.
- Select a target window.
- Confirm active backend.
- View via WebSocket.
- Switch to MJPEG.
- Confirm client counts and active pipelines change.
- Confirm runtime and FPS update.

## Implementation Notes

Use Python for orchestration and the web service. Keep the capture backend interface narrow so native NVIDIA or Windows code can be added later without changing the monitor or WebSocket API.

Implemented first-version dependencies:

- Pillow or OpenCV for JPEG encoding.
- pywin32 or equivalent for window enumeration.
- Optional `dxcam` for DXGI Desktop Duplication.
- Python standard-library HTTP server and a minimal WebSocket sender for local serving.

The first version should avoid pretending the NVIDIA backend is complete if no SDK binding exists. A clear unavailable status is better than an unstable partial implementation.
