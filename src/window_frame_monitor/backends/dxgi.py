from __future__ import annotations

import importlib.util
import sys
from time import perf_counter_ns
from typing import TypeAlias

from PIL import Image

from window_frame_monitor.models import CapturedFrame, WindowInfo


MonitorBounds: TypeAlias = tuple[int, int, int, int]
Region: TypeAlias = tuple[int, int, int, int]


class DxgiDesktopDuplicationBackend:
    name = "dxgi"

    def __init__(self) -> None:
        self._camera = None
        self._window: WindowInfo | None = None
        self._frame_id = 0
        self._output_bounds: MonitorBounds | None = None
        self._last_array = None

    def is_available(self) -> tuple[bool, str | None]:
        if sys.platform != "win32":
            return False, "DXGI Desktop Duplication is only available on Windows."
        if importlib.util.find_spec("dxcam") is None:
            return (
                False,
                "Python DXGI binding 'dxcam' is not installed. Install dxcam or provide a native Desktop Duplication binding.",
            )
        return True, None

    def start(self, window: WindowInfo) -> None:
        if window.minimized:
            raise RuntimeError("Cannot capture a minimized window.")
        if window.width <= 0 or window.height <= 0:
            raise RuntimeError("Cannot capture a window with empty bounds.")

        import dxcam

        monitors = _list_monitor_bounds()
        output_idx, output_bounds = _select_monitor_for_window(window, monitors)
        self._window = window
        self._output_bounds = output_bounds
        self._frame_id = 0
        self._camera = dxcam.create(device_idx=0, output_idx=output_idx, output_color="RGB", processor_backend="numpy")

    def get_frame(self) -> CapturedFrame:
        if self._camera is None or self._window is None or self._output_bounds is None:
            raise RuntimeError("DxgiDesktopDuplicationBackend.start() must be called before get_frame().")

        self._frame_id += 1
        region = _to_output_region(self._window, self._output_bounds)
        array = self._camera.grab(region=region)
        reused = False
        if array is None:
            if self._last_array is None:
                raise RuntimeError("DXGI Desktop Duplication returned no initial frame before timeout.")
            array = self._last_array
            reused = True
        else:
            self._last_array = array

        image = Image.fromarray(array, "RGB")
        return CapturedFrame(
            frame_id=self._frame_id,
            timestamp_ns=perf_counter_ns(),
            width=image.width,
            height=image.height,
            rgb_bytes=image.tobytes(),
            backend=self.name,
            reused=reused,
        )

    def stop(self) -> None:
        if self._camera is not None and hasattr(self._camera, "release"):
            self._camera.release()
        self._camera = None
        self._window = None
        self._output_bounds = None
        self._last_array = None


def _list_monitor_bounds() -> list[MonitorBounds]:
    if sys.platform != "win32":
        return [(0, 0, 0, 0)]

    import win32api

    monitors: list[MonitorBounds] = []
    for monitor, _hdc, _rect in win32api.EnumDisplayMonitors():
        info = win32api.GetMonitorInfo(monitor)
        monitors.append(tuple(info["Monitor"]))
    if not monitors:
        raise RuntimeError("No Windows display monitors were found for DXGI capture.")
    return monitors


def _select_monitor_for_window(window: WindowInfo, monitors: list[MonitorBounds]) -> tuple[int, MonitorBounds]:
    window_bounds = (window.x, window.y, window.x + window.width, window.y + window.height)
    best_idx = -1
    best_area = 0
    best_bounds: MonitorBounds | None = None

    for idx, bounds in enumerate(monitors):
        area = _intersection_area(window_bounds, bounds)
        if area > best_area:
            best_idx = idx
            best_area = area
            best_bounds = bounds

    if best_bounds is None:
        raise ValueError(
            f"Target window '{window.title}' does not intersect any DXGI output. "
            f"Window bounds={window_bounds}, outputs={monitors}."
        )
    return best_idx, best_bounds


def _to_output_region(window: WindowInfo, output_bounds: MonitorBounds) -> Region:
    output_left, output_top, output_right, output_bottom = output_bounds
    left = max(window.x, output_left) - output_left
    top = max(window.y, output_top) - output_top
    right = min(window.x + window.width, output_right) - output_left
    bottom = min(window.y + window.height, output_bottom) - output_top
    if right <= left or bottom <= top:
        raise ValueError(
            f"Target window '{window.title}' has no capturable area inside DXGI output {output_bounds}."
        )
    return left, top, right, bottom


def _intersection_area(a: MonitorBounds, b: MonitorBounds) -> int:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[2], b[2])
    bottom = min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)
