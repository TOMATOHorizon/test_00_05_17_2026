from pathlib import Path

from window_frame_monitor.remote_stream import (
    FrameChangeDetector,
    RemoteFrameStore,
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


def test_remote_frame_store_writes_latest_frame_and_state(tmp_path: Path):
    store = RemoteFrameStore(output_dir=tmp_path, width=16, height=16)
    frame = bytes([80, 120, 160]) * (16 * 16)

    store.update_frame(frame)
    state = store.state()

    assert state["frame_count"] == 1
    assert state["latest_frame_path"] == str(tmp_path / "latest.jpg")
    assert (tmp_path / "latest.jpg").exists()
    assert (tmp_path / "state.json").exists()
