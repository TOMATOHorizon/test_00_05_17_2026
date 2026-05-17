from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from collections import deque
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from time import perf_counter_ns
from typing import BinaryIO

from PIL import Image

from window_frame_monitor.frame_hub import FrameHub
from window_frame_monitor.models import CapturedFrame
from window_frame_monitor.video import (
    H264Settings,
    build_ffmpeg_h264_command,
    build_ffmpeg_h264_decoder_command,
    ffmpeg_available,
    h264_probe_output_dir,
)


@dataclass
class H264ProbeResult:
    ok: bool
    detail: str
    output_dir: str
    latest_frame_path: str
    stats_path: str
    duration_s: float
    submitted_frames: int
    decoded_frames: int
    average_latency_ms: float
    maximum_latency_ms: float
    minimum_latency_ms: float
    encoder: str
    bitrate_kbps: int
    output_width: int
    output_height: int
    fps: int


async def run_h264_probe(
    hub: FrameHub,
    *,
    duration_s: float = 3.0,
    base_dir: str | Path = ".",
) -> H264ProbeResult:
    if not ffmpeg_available():
        output_dir = h264_probe_output_dir(base_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        result = H264ProbeResult(
            ok=False,
            detail="ffmpeg is not available; H.264 encode/decode test cannot run.",
            output_dir=str(output_dir),
            latest_frame_path=str(output_dir / "latest.jpg"),
            stats_path=str(output_dir / "stats.json"),
            duration_s=0.0,
            submitted_frames=0,
            decoded_frames=0,
            average_latency_ms=0.0,
            maximum_latency_ms=0.0,
            minimum_latency_ms=0.0,
            encoder=hub.h264_settings.encoder,
            bitrate_kbps=hub.h264_settings.bitrate_kbps,
            output_width=hub.h264_settings.width,
            output_height=hub.h264_settings.height,
            fps=hub.h264_settings.fps,
        )
        _write_json(output_dir / "stats.json", asdict(result))
        return result

    output_dir = h264_probe_output_dir(base_dir)
    settings = hub.h264_settings
    probe = H264RoundTripProbe(settings=settings, output_dir=output_dir)
    last_frame_id = 0
    deadline_ns = perf_counter_ns() + int(duration_s * 1_000_000_000)
    await hub.add_h264_probe_client()
    probe.start()
    try:
        while perf_counter_ns() < deadline_ns:
            result = await hub.wait_for_next_frame(after_frame_id=last_frame_id)
            if result is None:
                await asyncio.sleep(0)
                continue
            last_frame_id, frame = result
            await asyncio.to_thread(probe.submit_frame, frame)
    finally:
        await hub.remove_h264_probe_client()
        final = await asyncio.to_thread(probe.close)
    return final


class H264RoundTripProbe:
    def __init__(self, *, settings: H264Settings, output_dir: Path) -> None:
        self._settings = settings
        self._output_dir = output_dir
        self._frame_size = settings.width * settings.height * 3
        self._timestamps: deque[int] = deque()
        self._latencies_ms: list[float] = []
        self._submitted_frames = 0
        self._decoded_frames = 0
        self._started_ns = 0
        self._lock = threading.Lock()
        self._encoder: subprocess.Popen[bytes] | None = None
        self._decoder: subprocess.Popen[bytes] | None = None
        self._pump_thread: threading.Thread | None = None
        self._decode_thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._started_ns = perf_counter_ns()
        self._encoder = subprocess.Popen(
            build_ffmpeg_h264_command(self._settings),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._decoder = subprocess.Popen(
            build_ffmpeg_h264_decoder_command(self._settings),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self._encoder.stdout is not None
        assert self._decoder.stdin is not None
        assert self._decoder.stdout is not None
        self._pump_thread = threading.Thread(
            target=self._pump_bitstream,
            args=(self._encoder.stdout, self._decoder.stdin),
            daemon=True,
        )
        self._decode_thread = threading.Thread(target=self._read_decoded_frames, args=(self._decoder.stdout,), daemon=True)
        self._pump_thread.start()
        self._decode_thread.start()

    def submit_frame(self, frame: CapturedFrame) -> None:
        if self._encoder is None or self._encoder.stdin is None:
            return
        rgb_bytes = _resize_rgb(frame, self._settings.width, self._settings.height)
        with self._lock:
            self._timestamps.append(perf_counter_ns())
            self._submitted_frames += 1
        try:
            self._encoder.stdin.write(rgb_bytes)
            self._encoder.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._error = str(exc)

    def close(self) -> H264ProbeResult:
        self._close_encoder_stdin()
        self._join_thread(self._pump_thread)
        self._join_thread(self._decode_thread)
        self._terminate_process(self._encoder)
        self._terminate_process(self._decoder)
        duration_s = (perf_counter_ns() - self._started_ns) / 1_000_000_000 if self._started_ns else 0.0
        result = self._result(duration_s)
        _write_json(self._output_dir / "stats.json", asdict(result))
        return result

    def _pump_bitstream(self, source: BinaryIO, destination: BinaryIO) -> None:
        try:
            while True:
                read1 = getattr(source, "read1", None)
                chunk = read1(4096) if read1 else source.read(4096)
                if not chunk:
                    break
                destination.write(chunk)
                destination.flush()
        except (BrokenPipeError, OSError) as exc:
            self._error = str(exc)
        finally:
            try:
                destination.close()
            except OSError:
                pass

    def _read_decoded_frames(self, source: BinaryIO) -> None:
        try:
            while True:
                raw = _read_exact(source, self._frame_size)
                if raw is None:
                    break
                image_available_ns = perf_counter_ns()
                with self._lock:
                    submitted_ns = self._timestamps.popleft() if self._timestamps else image_available_ns
                _write_latest_jpeg(self._output_dir / "latest.jpg", raw, self._settings.width, self._settings.height)
                finished_ns = perf_counter_ns()
                with self._lock:
                    self._decoded_frames += 1
                    self._latencies_ms.append((finished_ns - submitted_ns) / 1_000_000)
                self._write_live_stats()
        except OSError as exc:
            self._error = str(exc)

    def _write_live_stats(self) -> None:
        duration_s = (perf_counter_ns() - self._started_ns) / 1_000_000_000 if self._started_ns else 0.0
        _write_json(self._output_dir / "stats.json", asdict(self._result(duration_s)))

    def _result(self, duration_s: float) -> H264ProbeResult:
        with self._lock:
            latencies = list(self._latencies_ms)
            submitted_frames = self._submitted_frames
            decoded_frames = self._decoded_frames
        return H264ProbeResult(
            ok=decoded_frames > 0 and self._error is None,
            detail=self._error or ("completed" if decoded_frames else "no decoded frames were produced"),
            output_dir=str(self._output_dir),
            latest_frame_path=str(self._output_dir / "latest.jpg"),
            stats_path=str(self._output_dir / "stats.json"),
            duration_s=duration_s,
            submitted_frames=submitted_frames,
            decoded_frames=decoded_frames,
            average_latency_ms=sum(latencies) / len(latencies) if latencies else 0.0,
            maximum_latency_ms=max(latencies) if latencies else 0.0,
            minimum_latency_ms=min(latencies) if latencies else 0.0,
            encoder=self._settings.encoder,
            bitrate_kbps=self._settings.bitrate_kbps,
            output_width=self._settings.width,
            output_height=self._settings.height,
            fps=self._settings.fps,
        )

    def _close_encoder_stdin(self) -> None:
        if self._encoder and self._encoder.stdin:
            try:
                self._encoder.stdin.close()
            except OSError:
                pass

    @staticmethod
    def _join_thread(thread: threading.Thread | None) -> None:
        if thread:
            thread.join(timeout=3)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()


def _resize_rgb(frame: CapturedFrame, width: int, height: int) -> bytes:
    if frame.width == width and frame.height == height:
        return frame.rgb_bytes
    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb_bytes)
    resized = image.resize((width, height), Image.Resampling.BILINEAR)
    return resized.tobytes()


def _write_latest_jpeg(path: Path, raw: bytes, width: int, height: int) -> None:
    image = Image.frombytes("RGB", (width, height), raw)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    path.write_bytes(buffer.getvalue())


def _read_exact(source: BinaryIO, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = source.read(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
