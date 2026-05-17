from __future__ import annotations

import ctypes
from pathlib import Path

from window_frame_monitor.models import CapturedFrame, WindowInfo


class NvidiaNvFbcCaptureBackend:
    name = "nvfbc"

    def __init__(self) -> None:
        self._dll_path = self._find_capture_sdk_dll()
        self._reason = self._build_unavailable_reason()

    def is_available(self) -> tuple[bool, str | None]:
        return False, self._reason

    def start(self, window: WindowInfo) -> None:
        raise RuntimeError(self._reason)

    def get_frame(self) -> CapturedFrame:
        raise RuntimeError(self._reason)

    def stop(self) -> None:
        return None

    def _build_unavailable_reason(self) -> str:
        if self._dll_path is None:
            return (
                "NVIDIA Capture SDK / NvFBC DLL was not found. "
                "Install the NVIDIA Capture SDK and expose NvFBC64.dll to PATH or NVIDIA_CAPTURE_SDK_PATH."
            )
        try:
            ctypes.WinDLL(str(self._dll_path))
        except OSError as exc:
            return f"NVIDIA Capture SDK DLL was found at {self._dll_path}, but could not be loaded: {exc}"
        return (
            f"NVIDIA Capture SDK DLL was found at {self._dll_path}, "
            "but the native NvFBC Python binding is not implemented yet."
        )

    def _find_capture_sdk_dll(self) -> Path | None:
        candidates: list[Path] = []
        import os

        sdk_path = os.environ.get("NVIDIA_CAPTURE_SDK_PATH")
        if sdk_path:
            root = Path(sdk_path)
            candidates.extend(root.rglob("NvFBC64.dll"))
            candidates.extend(root.rglob("NvFBC.dll"))

        for entry in os.environ.get("PATH", "").split(os.pathsep):
            if not entry:
                continue
            directory = Path(entry)
            candidates.append(directory / "NvFBC64.dll")
            candidates.append(directory / "NvFBC.dll")

        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue
        return None


NvidiaWindowCaptureBackend = NvidiaNvFbcCaptureBackend
