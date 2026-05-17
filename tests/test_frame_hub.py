import asyncio

from window_frame_monitor.backends.test_pattern import TestPatternBackend
from window_frame_monitor.frame_hub import FrameHub
from window_frame_monitor.models import WindowInfo


def test_frame_hub_idles_without_consumers():
    asyncio.run(_frame_hub_idles_without_consumers())


async def _frame_hub_idles_without_consumers():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await asyncio.sleep(0.05)
    stats = hub.snapshot_stats()
    await hub.close()

    assert stats.capture_active is False
    assert stats.frame_id == 0


def test_frame_hub_captures_with_websocket_consumer():
    asyncio.run(_frame_hub_captures_with_websocket_consumer())


async def _frame_hub_captures_with_websocket_consumer():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await hub.add_websocket_client()
    await asyncio.sleep(0.12)
    frame = await hub.latest_jpeg()
    await hub.remove_websocket_client()
    stats = hub.snapshot_stats()
    await hub.close()

    assert frame is not None
    assert stats.websocket_clients == 0


def test_frame_hub_captures_with_h264_probe_consumer():
    asyncio.run(_frame_hub_captures_with_h264_probe_consumer())


async def _frame_hub_captures_with_h264_probe_consumer():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await hub.add_h264_probe_client()
    result = await hub.wait_for_next_frame()
    await hub.remove_h264_probe_client()
    stats = hub.snapshot_stats()
    await hub.close()

    assert result is not None
    assert result[1].rgb_bytes
    assert stats.capture_active is False


def test_frame_hub_matches_h264_output_to_target_size():
    asyncio.run(_frame_hub_matches_h264_output_to_target_size())


async def _frame_hub_matches_h264_output_to_target_size():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test", width=801, height=599))
    await hub.match_h264_output_to_source()
    stats = hub.snapshot_stats()
    await hub.close()

    assert stats.output_width == 802
    assert stats.output_height == 600


def test_frame_hub_matches_h264_output_to_latest_frame_size_when_available():
    asyncio.run(_frame_hub_matches_h264_output_to_latest_frame_size_when_available())


async def _frame_hub_matches_h264_output_to_latest_frame_size_when_available():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test", width=640, height=360))
    await hub.add_h264_probe_client()
    await hub.wait_for_next_frame()
    await hub.match_h264_output_to_source()
    await hub.remove_h264_probe_client()
    stats = hub.snapshot_stats()
    await hub.close()

    assert stats.output_width == 32
    assert stats.output_height == 18


def test_frame_hub_records_capture_errors_without_crashing_task():
    asyncio.run(_frame_hub_records_capture_errors_without_crashing_task())


async def _frame_hub_records_capture_errors_without_crashing_task():
    hub = FrameHub(backends=[_FailingBackend()], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await hub.add_websocket_client()
    await asyncio.sleep(0.12)
    stats = hub.snapshot_stats()
    await hub.close()

    assert stats.capture_active is False
    assert "boom" in (stats.backend_reason or "")


def test_frame_hub_stop_capture_resets_consumers_and_stops_backend():
    asyncio.run(_frame_hub_stop_capture_resets_consumers_and_stops_backend())


async def _frame_hub_stop_capture_resets_consumers_and_stops_backend():
    backend = TestPatternBackend(width=32, height=18)
    hub = FrameHub(backends=[backend], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await hub.add_websocket_client()
    await asyncio.sleep(0.08)
    await hub.stop_capture(clear_clients=True)
    stats = hub.snapshot_stats()
    await hub.close()

    assert stats.capture_active is False
    assert stats.websocket_clients == 0
    assert stats.mjpeg_clients == 0


def test_frame_hub_refresh_capture_keeps_target_and_restarts_cleanly():
    asyncio.run(_frame_hub_refresh_capture_keeps_target_and_restarts_cleanly())


async def _frame_hub_refresh_capture_keeps_target_and_restarts_cleanly():
    hub = FrameHub(backends=[TestPatternBackend(width=32, height=18)], target_fps=30)

    await hub.set_target(WindowInfo(hwnd=0, title="test"))
    await hub.add_websocket_client()
    await asyncio.sleep(0.08)
    await hub.refresh_capture()
    await hub.add_websocket_client()
    await asyncio.sleep(0.08)
    frame = await hub.latest_jpeg()
    await hub.close()

    assert frame is not None


class _FailingBackend:
    name = "windows"

    def is_available(self):
        return True, None

    def start(self, window):
        return None

    def get_frame(self):
        raise RuntimeError("boom")

    def stop(self):
        return None
