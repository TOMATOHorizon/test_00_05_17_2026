import numpy as np

from window_frame_monitor.backends.dxgi import DxgiDesktopDuplicationBackend, _select_monitor_for_window, _to_output_region
from window_frame_monitor.models import WindowInfo


def test_dxgi_region_translates_virtual_screen_coordinates_to_output_coordinates():
    window = WindowInfo(hwnd=1, title="right screen", x=3500, y=100, width=400, height=300)
    monitors = [(0, 0, 3440, 1440), (3440, 0, 5360, 1080)]

    output_idx, bounds = _select_monitor_for_window(window, monitors)
    region = _to_output_region(window, bounds)

    assert output_idx == 1
    assert region == (60, 100, 460, 400)


def test_dxgi_region_clamps_window_that_partly_overlaps_monitor():
    window = WindowInfo(hwnd=1, title="edge", x=-20, y=10, width=120, height=80)
    monitors = [(0, 0, 3440, 1440)]

    output_idx, bounds = _select_monitor_for_window(window, monitors)
    region = _to_output_region(window, bounds)

    assert output_idx == 0
    assert region == (0, 10, 100, 90)


def test_dxgi_region_rejects_window_outside_all_outputs():
    window = WindowInfo(hwnd=1, title="outside", x=4000, y=2000, width=100, height=100)
    monitors = [(0, 0, 3440, 1440)]

    try:
        _select_monitor_for_window(window, monitors)
    except ValueError as exc:
        assert "does not intersect" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_dxgi_backend_reuses_previous_frame_when_no_new_frame_arrives():
    backend = DxgiDesktopDuplicationBackend()
    backend._camera = _StaticCamera()
    backend._window = WindowInfo(hwnd=1, title="static", x=0, y=0, width=2, height=2)
    backend._output_bounds = (0, 0, 10, 10)

    first = backend.get_frame()
    second = backend.get_frame()

    assert first.rgb_bytes == second.rgb_bytes
    assert second.frame_id == first.frame_id + 1
    assert first.reused is False
    assert second.reused is True


class _StaticCamera:
    def __init__(self):
        self.calls = 0

    def grab(self, region):
        self.calls += 1
        if self.calls == 1:
            return np.zeros((2, 2, 3), dtype=np.uint8)
        return None
