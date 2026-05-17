from window_frame_monitor.stats import StatsTracker


def test_stats_tracker_records_runtime_and_frame_times():
    tracker = StatsTracker(now_ns=lambda: 0)

    tracker.mark_frame(frame_id=1, frame_started_ns=0, frame_finished_ns=10_000_000)
    snapshot = tracker.snapshot()

    assert snapshot.frame_id == 1
    assert snapshot.frame_time_ms == 10.0
    assert snapshot.avg_frame_time_ms == 10.0
    assert snapshot.max_frame_time_ms == 10.0


def test_stats_tracker_counts_dropped_frames():
    tracker = StatsTracker(now_ns=lambda: 50_000_000)

    tracker.mark_frame(frame_id=1, frame_started_ns=0, frame_finished_ns=10_000_000)
    tracker.mark_frame(frame_id=3, frame_started_ns=20_000_000, frame_finished_ns=30_000_000)
    snapshot = tracker.snapshot()

    assert snapshot.dropped_frames == 1
    assert snapshot.frame_id == 3


def test_stats_tracker_records_pipeline_timings():
    tracker = StatsTracker(now_ns=lambda: 100_000_000)

    tracker.mark_frame(
        frame_id=1,
        frame_started_ns=0,
        frame_finished_ns=80_000_000,
        capture_ms=20.0,
        encode_ms=30.0,
        serialize_ms=5.0,
        send_ms=25.0,
    )
    snapshot = tracker.snapshot()

    assert snapshot.capture_ms == 20.0
    assert snapshot.encode_ms == 30.0
    assert snapshot.serialize_ms == 5.0
    assert snapshot.send_ms == 25.0


def test_stats_tracker_records_frame_reuse_counts():
    tracker = StatsTracker(now_ns=lambda: 100_000_000)

    tracker.mark_frame(frame_id=1, frame_started_ns=0, frame_finished_ns=10_000_000, reused_frame=False)
    tracker.mark_frame(frame_id=2, frame_started_ns=10_000_000, frame_finished_ns=20_000_000, reused_frame=True)
    snapshot = tracker.snapshot()

    assert snapshot.new_frame_count == 1
    assert snapshot.reused_frame_count == 1


def test_stats_tracker_reports_one_second_new_and_reused_rates():
    now = 2_100_000_000
    tracker = StatsTracker(now_ns=lambda: now)

    tracker.mark_frame(frame_id=1, frame_started_ns=0, frame_finished_ns=500_000_000, reused_frame=False)
    tracker.mark_frame(frame_id=2, frame_started_ns=0, frame_finished_ns=1_200_000_000, reused_frame=False)
    tracker.mark_frame(frame_id=3, frame_started_ns=0, frame_finished_ns=1_500_000_000, reused_frame=True)
    tracker.mark_frame(frame_id=4, frame_started_ns=0, frame_finished_ns=2_000_000_000, reused_frame=True)
    snapshot = tracker.snapshot()

    assert snapshot.new_frames_per_s == 1
    assert snapshot.reused_frames_per_s == 2


def test_stats_tracker_runtime_only_counts_active_capture_time():
    now = 0
    tracker = StatsTracker(now_ns=lambda: now)

    tracker.set_runtime_state(
        capture_active=False,
        websocket_clients=0,
        mjpeg_clients=0,
        active_pipelines=[],
        active_backend=None,
        backend_reason=None,
    )
    now = 10_000_000_000
    assert tracker.snapshot().runtime_s == 0.0

    tracker.set_runtime_state(
        capture_active=True,
        websocket_clients=1,
        mjpeg_clients=0,
        active_pipelines=["websocket"],
        active_backend="dxgi",
        backend_reason=None,
    )
    now = 12_500_000_000
    assert tracker.snapshot().runtime_s == 2.5

    tracker.set_runtime_state(
        capture_active=False,
        websocket_clients=0,
        mjpeg_clients=0,
        active_pipelines=[],
        active_backend="dxgi",
        backend_reason=None,
    )
    now = 20_000_000_000
    assert tracker.snapshot().runtime_s == 2.5
