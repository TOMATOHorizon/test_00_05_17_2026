from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_monitor_metrics_are_grouped_and_logs_are_scrollable():
    html = (ROOT / "src/window_frame_monitor/static/monitor.html").read_text(encoding="utf-8")
    css = (ROOT / "src/window_frame_monitor/static/styles.css").read_text(encoding="utf-8")

    assert 'data-group="runtime"' in html
    assert 'data-group="timing"' in html
    assert 'data-group="frames"' in html
    assert 'data-group="connections"' in html
    assert ".log-panel pre" in css
    assert "max-height:" in css
    assert "overflow-y: auto" in css


def test_monitor_exposes_h264_probe_controls():
    html = (ROOT / "src/window_frame_monitor/static/monitor.html").read_text(encoding="utf-8")
    js = (ROOT / "src/window_frame_monitor/static/monitor.js").read_text(encoding="utf-8")

    assert 'id="run-h264-test"' in html
    assert 'id="match-source-output"' in html
    assert 'id="h264-test-status"' in html
    assert 'id="h264-test-latency"' in html
    assert "match_source_output" in js
    assert "/api/h264-test/start" in js
