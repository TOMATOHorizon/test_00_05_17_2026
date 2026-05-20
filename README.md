# Window Frame Monitor

Python local web monitor for selected-window frame capture.

The first version exposes:

- browser monitor at `http://127.0.0.1:8765/`
- WebSocket frame stream at `/ws/frames`
- MJPEG stream at `/stream.mjpg`
- stats at `/api/stats`
- window list at `/api/windows`

NVIDIA Capture SDK / NVFBC is the preferred backend, followed by a DXGI Desktop Duplication backend, then a simple Windows screenshot fallback. The prototype reports unavailable native backends clearly instead of silently pretending they are active.

## Run

```powershell
python -m pip install -e ".[dev,windows]"
window-frame-monitor --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

For a guaranteed local preview without relying on a real window capture backend:

```powershell
window-frame-monitor --host 127.0.0.1 --port 8765 --test-backend
```

The current server uses Python standard-library HTTP and WebSocket handling so the prototype can run even when FastAPI/Uvicorn are not installed.

## Minimal Remote H.264 Stream

This project also includes a separate sender/receiver prototype for testing a remote Agent-style video path:

```text
local capture -> H.264 encode -> TCP stream -> remote decode -> latest frame + change stats
```

Start the receiver on the target server:

```powershell
py -3.12 -m window_frame_monitor.remote_stream receiver --stream-host 0.0.0.0 --stream-port 8766 --dashboard-host 127.0.0.1 --dashboard-port 8770
```

For a lower-CPU receiver test, reduce background analysis and avoid writing a JPEG snapshot every decoded frame:

```powershell
py -3.12 -m window_frame_monitor.remote_stream receiver --stream-host 0.0.0.0 --stream-port 8766 --dashboard-host 127.0.0.1 --dashboard-port 8770 --decoder cuda --change-fps 5 --snapshot-fps 0 --state-fps 1
```

For the closest "Agent receiver" mode, keep decoded frames as in-memory `yuv420p` and only convert to JPEG/RGB when `/latest.jpg` is requested:

```powershell
py -3.12 -m window_frame_monitor.remote_stream receiver --stream-host 0.0.0.0 --stream-port 8766 --dashboard-host 127.0.0.1 --dashboard-port 8770 --decoder cuda --frame-format yuv420p --change-fps 5 --snapshot-fps 0 --state-fps 1
```

`--decoder cuda` asks FFmpeg to use NVIDIA `h264_cuvid` hardware decoding. If the target server's FFmpeg build does not include `h264_cuvid`, use `--decoder software`.

Open the receiver dashboard:

```text
http://127.0.0.1:8770/
```

The receiver also includes a first-pass Minecraft visual agent loop. It uses the latest decoded frame, asks the configured Ollama VLM for a strict JSON decision, queues safe whitelisted key/mouse actions, and exposes pause/resume controls on the dashboard. The loop is enabled by default and can be paused at startup:

```powershell
py -3.12 -m window_frame_monitor.remote_stream receiver --stream-host 0.0.0.0 --stream-port 8766 --dashboard-port 8770 --no-agent-enabled
```

The dashboard includes a priority goal input. When a goal is set there, each agent tick asks the model to complete that goal first; clearing the goal returns the agent to its default Minecraft tree/survival objective. The agent parser also attempts to repair common malformed JSON responses, such as missing commas between fields, before rejecting the model output.

List windows on the sending machine:

```powershell
py -3.12 -m window_frame_monitor.remote_stream list-windows
```

Send one selected window to the receiver:

```powershell
py -3.12 -m window_frame_monitor.remote_stream sender --server-host TARGET_SERVER_IP --stream-port 8766 --hwnd WINDOW_HANDLE --fps 24 --encoder h264_nvenc --bitrate-kbps 4000
```

To let the sender poll and execute queued agent actions on the game machine:

```powershell
py -3.12 -m window_frame_monitor.remote_stream sender --server-host TARGET_SERVER_IP --stream-port 8766 --hwnd WINDOW_HANDLE --fps 24 --encoder h264_nvenc --agent-control-url http://RECEIVER_IP:8770 --execute-agent-actions
```

For a local end-to-end test without a real capture target:

```powershell
py -3.12 -m window_frame_monitor.remote_stream receiver --stream-host 127.0.0.1 --stream-port 8766 --dashboard-port 8770
py -3.12 -m window_frame_monitor.remote_stream sender --server-host 127.0.0.1 --stream-port 8766 --test-backend --duration-s 5 --fps 24 --encoder h264_nvenc
```

The receiver continuously decodes the stream, writes `runtime/remote_receiver/latest.jpg`, writes `runtime/remote_receiver/state.json`, and computes a lightweight frame-change score. This raw TCP prototype is intended for trusted LAN testing only; add authentication/encryption before using it across an untrusted network.

## Backend order

1. `nvfbc`: checks for NVIDIA Capture SDK / `NvFBC64.dll`. A native Python binding is still required before this can capture frames.
2. `dxgi`: uses `dxcam` when installed, which wraps Windows Desktop Duplication.
3. `windows`: uses `PIL.ImageGrab` as a slower but broadly available fallback.
4. `test-pattern`: synthetic frames for local verification.

To try the DXGI backend:

```powershell
py -3.12 -m pip install dxcam
py -3.12 -m window_frame_monitor.cli --host 127.0.0.1 --port 8765
```

To let the NVFBC backend detect an SDK install, either put `NvFBC64.dll` on `PATH` or set:

```powershell
$env:NVIDIA_CAPTURE_SDK_PATH = "C:\Path\To\NVIDIA Capture SDK"
```

## Troubleshooting

`ModuleNotFoundError: No module named 'cv2'` from `dxcam`:

- Older code created dxcam with its default `processor_backend="cv2"`.
- The current DXGI backend uses `processor_backend="numpy"` to avoid requiring OpenCV when dxcam's NumPy kernels are available.
- Restart the monitor process after updating the code.

`ValueError: Invalid Region: Region should be in 3440x1440`:

- dxcam expects capture regions in the local coordinate space of one DXGI output.
- Windows window coordinates are virtual desktop coordinates and can be negative, on another monitor, or partly outside the selected output.
- The current DXGI backend chooses the monitor that intersects the window most and translates/clamps the region before calling dxcam.

`DXCamera instance already exists...`:

- dxcam reuses a singleton camera for the same device/output/backend.
- The current backend calls `release()` during stop; restart the monitor if an old process is still holding a previous camera instance.
