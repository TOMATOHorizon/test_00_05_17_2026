from window_frame_monitor.video import (
    H264Settings,
    build_ffmpeg_h264_command,
    build_ffmpeg_h264_decoder_command,
    h264_probe_output_dir,
)


def test_h264_settings_build_nvenc_low_latency_command():
    settings = H264Settings(width=1280, height=720, fps=60, bitrate_kbps=6000, encoder="h264_nvenc")

    command = build_ffmpeg_h264_command(settings)

    assert command[:2] == ["ffmpeg", "-hide_banner"]
    assert "-f" in command
    assert "rawvideo" in command
    assert "-s" in command
    assert "1280x720" in command
    assert "-r" in command
    assert "60" in command
    assert "-c:v" in command
    assert "h264_nvenc" in command
    assert "-b:v" in command
    assert "6000k" in command
    assert "-tune" in command
    assert "ull" in command
    assert "-flush_packets" in command
    assert command[-3:] == ["-f", "h264", "pipe:1"]


def test_h264_settings_clamp_invalid_values():
    settings = H264Settings(width=1, height=2, fps=999, bitrate_kbps=1)

    assert settings.width == 16
    assert settings.height == 16
    assert settings.fps == 240
    assert settings.bitrate_kbps == 250


def test_h264_settings_support_software_x264_command():
    settings = H264Settings(width=640, height=360, fps=24, bitrate_kbps=1200, encoder="libx264")

    command = build_ffmpeg_h264_command(settings)

    assert "libx264" in command
    assert "-preset" in command
    assert "veryfast" in command
    assert "-tune" in command
    assert "zerolatency" in command


def test_h264_decoder_command_outputs_raw_rgb_frames():
    settings = H264Settings(width=640, height=360, fps=24, bitrate_kbps=1200)

    command = build_ffmpeg_h264_decoder_command(settings)

    assert command[:2] == ["ffmpeg", "-hide_banner"]
    assert "-flags" in command
    assert "low_delay" in command
    assert "-probesize" in command
    assert "-analyzeduration" in command
    assert "-f" in command
    assert "h264" in command
    assert "-pix_fmt" in command
    assert "rgb24" in command
    assert command[-1] == "pipe:1"


def test_h264_decoder_command_can_request_cuda_decoder():
    settings = H264Settings(width=640, height=360, fps=24, bitrate_kbps=1200)

    command = build_ffmpeg_h264_decoder_command(settings, decoder="cuda")

    assert "-c:v" in command
    assert "h264_cuvid" in command


def test_h264_probe_output_dir_is_separate_runtime_folder(tmp_path):
    output_dir = h264_probe_output_dir(tmp_path)

    assert output_dir == tmp_path / "runtime" / "h264_probe"
