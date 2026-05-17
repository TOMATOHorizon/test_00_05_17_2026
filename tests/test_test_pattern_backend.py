from window_frame_monitor.backends.dxgi import DxgiDesktopDuplicationBackend
from window_frame_monitor.backends.nvidia import NvidiaWindowCaptureBackend
from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.models import WindowInfo


def test_test_pattern_backend_emits_changing_frames():
    backend = TestPatternBackend(width=64, height=36)
    backend.start(WindowInfo(hwnd=0, title="test"))

    first = backend.get_frame()
    second = backend.get_frame()

    assert first.width == 64
    assert first.height == 36
    assert first.rgb_bytes != second.rgb_bytes
    assert second.frame_id == first.frame_id + 1


def test_nvidia_backend_reports_unavailable_without_binding():
    backend = NvidiaWindowCaptureBackend()

    available, reason = backend.is_available()

    assert available is False
    assert reason


def test_dxgi_backend_reports_availability_status():
    backend = DxgiDesktopDuplicationBackend()

    available, reason = backend.is_available()

    assert isinstance(available, bool)
    if not available:
        assert reason
