from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess


H264_ENCODERS = {"h264_nvenc", "libx264"}


@dataclass(frozen=True)
class H264Settings:
    width: int = 1280
    height: int = 720
    fps: int = 60
    bitrate_kbps: int = 6000
    encoder: str = "h264_nvenc"
    preset: str = "p4"

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", _even_clamp(self.width, 16, 7680))
        object.__setattr__(self, "height", _even_clamp(self.height, 16, 4320))
        object.__setattr__(self, "fps", max(1, min(240, int(self.fps))))
        object.__setattr__(self, "bitrate_kbps", max(250, min(100_000, int(self.bitrate_kbps))))
        if self.encoder not in H264_ENCODERS:
            object.__setattr__(self, "encoder", "h264_nvenc")


def build_ffmpeg_h264_command(settings: H264Settings) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{settings.width}x{settings.height}",
        "-r",
        str(settings.fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        settings.encoder,
        "-b:v",
        f"{settings.bitrate_kbps}k",
        "-g",
        str(settings.fps),
    ]
    if settings.encoder == "h264_nvenc":
        command.extend(["-preset", settings.preset, "-tune", "ull", "-rc", "cbr", "-bf", "0"])
    else:
        command.extend(
            [
                "-preset",
                "veryfast",
                "-tune",
                "zerolatency",
                "-x264-params",
                f"keyint={settings.fps}:min-keyint={settings.fps}:scenecut=0",
            ]
        )
    command.extend(["-pix_fmt", "yuv420p", "-flush_packets", "1", "-f", "h264", "pipe:1"])
    return command


def build_ffmpeg_h264_decoder_command(
    settings: H264Settings,
    decoder: str = "software",
    pixel_format: str = "rgb24",
) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-flags",
        "low_delay",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-f",
        "h264",
    ]
    if decoder == "cuda":
        command.extend(["-c:v", "h264_cuvid"])
    command.extend(
        [
        "-i",
        "pipe:0",
        "-an",
        "-f",
        "rawvideo",
            "-pix_fmt",
            pixel_format,
            "pipe:1",
        ]
    )
    return command


def h264_probe_output_dir(base_dir: str | Path = ".") -> Path:
    return Path(base_dir) / "runtime" / "h264_probe"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def available_h264_encoders() -> list[str]:
    if not ffmpeg_available():
        return []
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    encoders = []
    for encoder in sorted(H264_ENCODERS):
        if encoder in result.stdout:
            encoders.append(encoder)
    return encoders


def _even_clamp(value: int, minimum: int, maximum: int) -> int:
    clamped = max(minimum, min(maximum, int(value)))
    if clamped % 2:
        clamped += 1
    return min(clamped, maximum)
