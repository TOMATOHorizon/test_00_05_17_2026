from __future__ import annotations

from time import perf_counter_ns

from PIL import Image, ImageDraw

from window_frame_monitor.models import CapturedFrame, WindowInfo


class TestPatternBackend:
    __test__ = False

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
