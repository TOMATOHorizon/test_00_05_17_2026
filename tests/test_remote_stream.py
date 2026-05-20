from pathlib import Path

from window_frame_monitor.remote_stream import (
    FrameChangeDetector,
    RemoteFrameStore,
    decoded_frame_size,
    make_stream_header,
    parse_stream_header,
)
from window_frame_monitor.video import H264Settings


def test_stream_header_round_trips_h264_settings():
    settings = H264Settings(width=854, height=481, fps=24, bitrate_kbps=2400, encoder="libx264")

    header = make_stream_header(settings)
    parsed = parse_stream_header(header)

    assert parsed.width == 854
    assert parsed.height == 482
    assert parsed.fps == 24
    assert parsed.bitrate_kbps == 2400
    assert parsed.encoder == "libx264"


def test_change_detector_reports_no_change_for_identical_frames():
    detector = FrameChangeDetector(width=16, height=16)
    frame = bytes([10, 20, 30]) * (16 * 16)

    first = detector.update(frame)
    second = detector.update(frame)

    assert first.change_score == 1.0
    assert second.change_score == 0.0
    assert second.changed_pixels_ratio == 0.0


def test_change_detector_reports_change_for_different_frames():
    detector = FrameChangeDetector(width=16, height=16)

    detector.update(bytes([0, 0, 0]) * (16 * 16))
    result = detector.update(bytes([255, 255, 255]) * (16 * 16))

    assert result.change_score > 0.5
    assert result.changed_pixels_ratio == 1.0


def test_change_detector_can_use_luma_plane_without_rgb_conversion():
    detector = FrameChangeDetector(width=16, height=16)

    detector.update_luma(bytes([0]) * (16 * 16))
    result = detector.update_luma(bytes([255]) * (16 * 16))

    assert result.change_score > 0.5
    assert result.changed_pixels_ratio == 1.0


def test_remote_frame_store_writes_latest_frame_and_state(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16, snapshot_fps=30)
    frame = bytes([80, 120, 160]) * (16 * 16)

    store.update_frame(frame)
    state = store.state()

    assert state["frame_count"] == 1
    assert state["latest_frame_path"] == str(tmp_path / "latest.jpg")
    assert (tmp_path / "latest.jpg").exists()
    assert (tmp_path / "state.json").exists()


def test_remote_frame_store_can_skip_per_frame_snapshots(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16, snapshot_fps=0)
    frame = bytes([80, 120, 160]) * (16 * 16)

    store.update_frame(frame)
    jpeg = store.latest_jpeg()

    assert not (tmp_path / "latest.jpg").exists()
    assert jpeg is not None


def test_remote_frame_store_throttles_change_detection(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16, change_fps=1, snapshot_fps=0)

    store.update_frame(bytes([0, 0, 0]) * (16 * 16))
    store.update_frame(bytes([255, 255, 255]) * (16 * 16))
    state = store.state()

    assert state["frame_count"] == 2
    assert state["change_score"] == 1.0


def test_remote_frame_store_reports_processed_change_fps(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16, change_fps=1_000_000, snapshot_fps=0)

    store.update_frame(bytes([0, 0, 0]) * (16 * 16))
    store.update_frame(bytes([255, 255, 255]) * (16 * 16))
    state = store.state()

    assert state["processed_frame_count"] == 2
    assert state["processed_fps"] == 2.0


def test_remote_frame_store_can_keep_yuv420p_latest_frame_in_memory(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16, snapshot_fps=0, pixel_format="yuv420p")
    y = bytes([96]) * (16 * 16)
    u = bytes([128]) * (8 * 8)
    v = bytes([128]) * (8 * 8)

    store.update_frame(y + u + v)
    jpeg = store.latest_jpeg()
    state = store.state()

    assert state["pixel_format"] == "yuv420p"
    assert state["frame_count"] == 1
    assert jpeg is not None
    assert not (tmp_path / "latest.jpg").exists()


def test_decoded_frame_size_supports_rgb_and_yuv420p():
    assert decoded_frame_size(width=16, height=16, pixel_format="rgb24") == 16 * 16 * 3
    assert decoded_frame_size(width=16, height=16, pixel_format="yuv420p") == 16 * 16 * 3 // 2


def test_receiver_dashboard_requests_latest_frame_manually():
    from window_frame_monitor.remote_stream import _dashboard_html

    html = _dashboard_html().decode("utf-8")

    assert 'id="get-latest"' in html
    assert "fetch('/latest.jpg?t='" in html
    assert "Processed FPS" in html
    assert "Received" in html
    assert "Received Total" in html
    assert 'id="describe-latest"' in html
    assert "LLM History" in html
    assert "fetch('/describe-latest'" in html
    assert 'id="agent-goal"' in html
    assert "user_goal" in html


def test_receiver_tracks_total_received_bytes():
    from window_frame_monitor.remote_stream import RemoteH264Receiver

    receiver = RemoteH264Receiver()

    receiver.mark_received_bytes(1024)
    receiver.mark_received_bytes(512)
    state = receiver.state()

    assert state["received_total_kib"] == 1.5


def test_receiver_records_vlm_error_when_no_frame_is_available():
    from window_frame_monitor.remote_stream import RemoteH264Receiver

    receiver = RemoteH264Receiver()

    event = receiver.describe_latest_frame()
    history = receiver.vlm_history()

    assert event["status"] == "error"
    assert "No frame available" in str(event["content"])
    assert history[-1] == event


def test_remote_frame_store_keeps_disconnect_detail(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16)

    store.update_status("disconnected", "stream ended")
    state = store.state()

    assert state["status"] == "disconnected"
    assert state["detail"] == "stream ended"
