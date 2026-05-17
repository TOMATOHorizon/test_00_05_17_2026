const preview = document.querySelector("#preview-image");
const windowSelect = document.querySelector("#window-select");
const backendStatus = document.querySelector("#backend-status");
const wsButton = document.querySelector("#source-ws");
const mjpegButton = document.querySelector("#source-mjpeg");
const systemLog = document.querySelector("#system-log");
const eventLog = document.querySelector("#event-log");
const fpsSelect = document.querySelector("#fps-select");
const customFps = document.querySelector("#custom-fps");
const h264Encoder = document.querySelector("#h264-encoder");
const h264Bitrate = document.querySelector("#h264-bitrate");
const outputWidth = document.querySelector("#output-width");
const outputHeight = document.querySelector("#output-height");
const runH264TestButton = document.querySelector("#run-h264-test");
const matchSourceOutputButton = document.querySelector("#match-source-output");

let socket = null;
let mode = "websocket";
let currentObjectUrl = null;
let selectedWindow = null;

function setText(id, value) {
  document.querySelector(id).textContent = value;
}

function applyStats(stats) {
  backendStatus.textContent = `Backend: ${stats.active_backend || "waiting"}${stats.backend_reason ? ` (${stats.backend_reason})` : ""}`;
  systemLog.textContent = [
    `backend: ${stats.active_backend || "waiting"}`,
    `reason: ${stats.backend_reason || "none"}`,
    `capture: ${stats.capture_active ? "active" : "idle"}`,
    `pipelines: ${stats.active_pipelines.length ? stats.active_pipelines.join(", ") : "none"}`,
    `clients: websocket=${stats.websocket_clients}, mjpeg=${stats.mjpeg_clients}`,
  ].join("\n");
  setText("#runtime", `${Number(stats.runtime_s).toFixed(1)}s`);
  setText("#fps", Number(stats.fps).toFixed(1));
  setText("#target-fps", stats.target_fps);
  setText("#frame-time", `${Number(stats.frame_time_ms).toFixed(1)} ms`);
  setText("#capture-ms", `${Number(stats.capture_ms).toFixed(1)} ms`);
  setText("#encode-ms", `${Number(stats.encode_ms).toFixed(1)} ms`);
  setText("#serialize-ms", `${Number(stats.serialize_ms).toFixed(1)} ms`);
  setText("#send-ms", `${Number(stats.send_ms).toFixed(1)} ms`);
  setText("#avg-frame-time", `${Number(stats.avg_frame_time_ms).toFixed(1)} ms`);
  setText("#max-frame-time", `${Number(stats.max_frame_time_ms).toFixed(1)} ms`);
  setText("#dropped", stats.dropped_frames);
  setText("#new-frames-per-s", stats.new_frames_per_s);
  setText("#reused-frames-per-s", stats.reused_frames_per_s);
  setText("#new-frames", stats.new_frame_count);
  setText("#reused-frames", stats.reused_frame_count);
  setText("#ws-clients", stats.websocket_clients);
  setText("#mjpeg-clients", stats.mjpeg_clients);
  setText("#capture-state", stats.capture_active ? "active" : "idle");
  setText("#pipelines", stats.active_pipelines.length ? stats.active_pipelines.join(", ") : "none");
  setText("#video-codec", stats.video_codec);
  setText("#h264-encoder-state", stats.h264_encoder);
  setText("#h264-bitrate-state", `${stats.h264_bitrate_kbps} kbps`);
  setText("#output-size-state", `${stats.output_width}x${stats.output_height}`);
}

function selectedTargetFps() {
  if (fpsSelect.value === "custom") {
    return Math.max(1, Math.min(240, Number(customFps.value) || 60));
  }
  return Number(fpsSelect.value);
}

async function applyFps() {
  const targetFps = selectedTargetFps();
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_fps: targetFps }),
  });
  const body = await response.json();
  if (!response.ok) {
    logEvent(body.detail || "failed to update fps");
    return;
  }
  applyStats(body.stats);
  logEvent(`target fps set to ${targetFps}`);
}

async function applyH264Settings() {
  const payload = {
    h264_encoder: h264Encoder.value,
    h264_bitrate_kbps: Number(h264Bitrate.value) || 6000,
    output_width: Number(outputWidth.value) || 1280,
    output_height: Number(outputHeight.value) || 720,
  };
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json();
  if (!response.ok) {
    logEvent(body.detail || "failed to update h264 settings");
    return;
  }
  applyStats(body.stats);
  logEvent(`h264 ${body.stats.h264_encoder} ${body.stats.h264_bitrate_kbps}kbps ${body.stats.output_width}x${body.stats.output_height}`);
}

async function matchSourceOutput() {
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ match_source_output: true }),
  });
  const body = await response.json();
  if (!response.ok) {
    logEvent(body.detail || "failed to match source output");
    return;
  }
  outputWidth.value = body.stats.output_width;
  outputHeight.value = body.stats.output_height;
  applyStats(body.stats);
  logEvent(`output matched source: ${body.stats.output_width}x${body.stats.output_height}`);
}

async function runH264Test() {
  runH264TestButton.disabled = true;
  setText("#h264-test-status", "running");
  logEvent("h264 test started for 3 seconds");
  try {
    const response = await fetch("/api/h264-test/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ duration_s: 3 }),
    });
    const body = await response.json();
    if (!response.ok) {
      setText("#h264-test-status", "failed");
      logEvent(body.detail || "h264 test failed");
      return;
    }
    const probe = body.probe;
    applyStats(body.stats);
    setText("#h264-test-status", probe.ok ? "complete" : "failed");
    setText("#h264-test-decoded", `${probe.decoded_frames}/${probe.submitted_frames}`);
    setText("#h264-test-latency", `${Number(probe.average_latency_ms).toFixed(1)} ms`);
    setText("#h264-test-max", `${Number(probe.maximum_latency_ms).toFixed(1)} ms`);
    setText("#h264-test-latest", probe.latest_frame_path || "none");
    logEvent(`h264 test ${probe.detail}: ${probe.decoded_frames} decoded, avg ${Number(probe.average_latency_ms).toFixed(1)}ms`);
  } finally {
    runH264TestButton.disabled = false;
  }
}

function logEvent(message) {
  const line = `${new Date().toLocaleTimeString()}  ${message}`;
  eventLog.textContent = [line, ...eventLog.textContent.split("\n").filter(Boolean)].slice(0, 12).join("\n");
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
  logEvent(`loaded ${windows.length} windows`);
}

async function selectWindow() {
  await stopCapture();
  const hwnd = Number(windowSelect.value);
  const response = await fetch("/api/target", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hwnd }),
  });
  const body = await response.json();
  if (!response.ok) {
    backendStatus.textContent = body.detail || "Failed to select window";
    logEvent(body.detail || "capture failed");
    return;
  }
  selectedWindow = hwnd;
  applyStats(body.stats);
  logEvent(`capture started via ${mode}`);
  startSelectedMode();
}

async function refreshStream() {
  if (selectedWindow === null) {
    logEvent("no active capture to refresh");
    return;
  }
  stopStreams();
  const response = await fetch("/api/capture/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  const body = await response.json();
  applyStats(body.stats);
  logEvent(`stream refreshed for ${mode}`);
  if (selectedWindow !== null) {
    startSelectedMode();
  }
}

async function stopCapture() {
  stopStreams();
  const response = await fetch("/api/capture/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  if (response.ok) {
    const body = await response.json();
    applyStats(body.stats);
  }
  logEvent("capture stopped");
}

function clearPreview() {
  preview.removeAttribute("src");
  if (currentObjectUrl) {
    URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = null;
  }
}

function stopStreams() {
  clearPreview();
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
  socket.onopen = () => logEvent("websocket connected");
  socket.onclose = () => logEvent("websocket closed");
}

function startMjpeg() {
  stopStreams();
  preview.src = `/stream.mjpg?cacheBust=${Date.now()}`;
  logEvent("mjpeg stream connected");
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

async function selectMode(nextMode) {
  mode = nextMode;
  wsButton.classList.toggle("active", mode === "websocket");
  mjpegButton.classList.toggle("active", mode === "mjpeg");
  await stopCapture();
  logEvent(`source set to ${mode}`);
}

async function pollStats() {
  const response = await fetch("/api/stats");
  applyStats(await response.json());
}

document.querySelector("#reload-windows").addEventListener("click", loadWindows);
document.querySelector("#select-window").addEventListener("click", selectWindow);
document.querySelector("#refresh-stream").addEventListener("click", refreshStream);
document.querySelector("#stop-capture").addEventListener("click", stopCapture);
document.querySelector("#apply-fps").addEventListener("click", applyFps);
document.querySelector("#apply-h264").addEventListener("click", applyH264Settings);
runH264TestButton.addEventListener("click", runH264Test);
matchSourceOutputButton.addEventListener("click", matchSourceOutput);
fpsSelect.addEventListener("change", () => {
  customFps.disabled = fpsSelect.value !== "custom";
});
wsButton.addEventListener("click", () => selectMode("websocket"));
mjpegButton.addEventListener("click", () => selectMode("mjpeg"));

loadWindows();
customFps.disabled = fpsSelect.value !== "custom";
setInterval(pollStats, 1000);
