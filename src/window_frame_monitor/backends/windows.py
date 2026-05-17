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
