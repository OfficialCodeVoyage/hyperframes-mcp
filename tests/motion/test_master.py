"""Mastering with a fake ffmpeg (a shell script): the commands it is given, the retry on a miss, the refusals."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from hyperframes_mcp.motion import master as mastering
from hyperframes_mcp.motion.master import Loudness, MasterError, master, measure
from tests.motion.master_fakes import PASS_ONE, FakeFfmpeg, summary


@pytest.fixture
def ffmpeg(tmp_path) -> FakeFfmpeg:
    (tmp_path / "bin").mkdir()
    return FakeFfmpeg(tmp_path / "bin")


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "raw.mp4"
    path.write_bytes(b"video")
    return path


def test_measure_reads_loudness_true_peak_and_range(ffmpeg, video):
    ffmpeg.measures(summary(-14.0, -1.4, 5.7))

    got = measure(video, ffmpeg=str(ffmpeg.path))

    assert got == Loudness(-14.0, -1.4, 5.7)
    [call] = ffmpeg.calls()
    assert "ebur128=peak=true:framelog=quiet" in call and "-map 0:a:0" in call


def test_master_runs_two_loudnorm_passes_and_returns_the_measurement_of_the_file(tmp_path, ffmpeg, video):
    ffmpeg.measures(summary(-14.02, -1.4))
    out = tmp_path / "out" / "final.mp4"

    got = master(video, out, ffmpeg=str(ffmpeg.path))

    assert got == Loudness(-14.0, -1.4, 5.0)
    assert out.read_text() == "mastered\n"
    first, second, check = ffmpeg.calls()
    assert "loudnorm=I=-14:TP=-1:LRA=11:print_format=json" in first
    # pass two applies pass one's numbers as one linear gain, then a limiter 0.5 dB under the ceiling at 48 kHz
    assert "measured_I=-21.50:measured_TP=-4.20:measured_LRA=6.10" in second
    assert "measured_thresh=-32.00:offset=0.04:linear=true" in second
    assert "loudnorm=I=-14.00:TP=-1:LRA=11" in second
    assert "alimiter=limit=0.8414:attack=5:release=80:level=false,aresample=48000" in second
    assert "-c:v copy" in second and "-c:a aac -b:a 256k" in second
    assert "-map 0:v -map 0:a:0" in second
    assert "ebur128" in check
    assert not [p for p in out.parent.iterdir() if p.name != "final.mp4"]


def test_the_targets_are_the_arguments(tmp_path, ffmpeg, video):
    ffmpeg.measures(summary(-16.0, -2.6))

    master(
        video,
        tmp_path / "o.mp4",
        lufs=-16.0,
        true_peak_db=-2.0,
        ffmpeg=str(ffmpeg.path),
    )

    first, second, _ = ffmpeg.calls()
    assert "loudnorm=I=-16:TP=-2:LRA=11:print_format=json" in first
    assert "loudnorm=I=-16.00:TP=-2:LRA=11" in second
    assert "limit=0.7499" in second


def test_a_miss_moves_the_target_by_the_error_and_runs_again(tmp_path, ffmpeg, video):
    ffmpeg.measures(summary(-15.0, -3.0), summary(-14.1, -1.2))

    got = master(video, tmp_path / "o.mp4", ffmpeg=str(ffmpeg.path))

    assert got.integrated_lufs == -14.1
    calls = ffmpeg.calls()
    assert len(calls) == 5  # measure, pass, check, pass, check
    assert "loudnorm=I=-14.00:" in calls[1]
    assert "loudnorm=I=-13.00:" in calls[3]  # the first result was 1 LU quiet


def test_a_true_peak_over_the_ceiling_lowers_the_target_even_when_the_loudness_is_right(tmp_path, ffmpeg, video):
    ffmpeg.measures(summary(-14.0, -0.6), summary(-14.1, -1.3))

    master(video, tmp_path / "o.mp4", ffmpeg=str(ffmpeg.path))

    # 0.4 dB over the ceiling: the target comes down by 0.4 plus 0.05
    assert "loudnorm=I=-14.45:" in ffmpeg.calls()[3]


def test_three_passes_that_miss_are_an_error_and_leave_no_file(tmp_path, ffmpeg, video):
    ffmpeg.measures(summary(-18.0, -3.0), summary(-17.0, -3.0), summary(-16.5, -3.0))
    out = tmp_path / "o.mp4"

    with pytest.raises(MasterError, match=r"after 3 passes the sound is -16.5 LUFS"):
        master(video, out, ffmpeg=str(ffmpeg.path))

    assert not out.exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


def test_a_video_with_no_audio_or_silent_sound_is_refused(tmp_path, video):
    no_audio = tmp_path / "no_audio"
    no_audio.mkdir()
    script = no_audio / "ffmpeg"
    script.write_text(
        "#!/bin/sh\necho \"Stream map '0:a:0' matches no streams.\" >&2\nexit 1\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    with pytest.raises(MasterError, match="the video has no audio stream"):
        master(video, tmp_path / "o.mp4", ffmpeg=str(script))

    (tmp_path / "silent").mkdir()
    silent = FakeFfmpeg(tmp_path / "silent")
    silent.pass_one({**PASS_ONE, "input_i": "-inf", "input_tp": "-inf"})
    with pytest.raises(MasterError, match="silent"):
        master(video, tmp_path / "o.mp4", ffmpeg=str(silent.path))

    silent.pass_one("no json here")
    with pytest.raises(MasterError, match="no usable loudness measurement"):
        master(video, tmp_path / "o.mp4", ffmpeg=str(silent.path))


def test_only_a_plain_local_video_file_is_opened(tmp_path, ffmpeg):
    text = tmp_path / "notes.txt"
    text.write_text("x")

    for bad in (tmp_path / "missing.mp4", text, "http://example.com/a.mp4", tmp_path):
        with pytest.raises(MasterError):
            master(bad, tmp_path / "o.mp4", ffmpeg=str(ffmpeg.path))
        with pytest.raises(MasterError):
            measure(bad, ffmpeg=str(ffmpeg.path))


def test_a_summary_without_loudness_is_an_error(ffmpeg, video):
    ffmpeg.measures("nothing useful\n")

    with pytest.raises(MasterError, match="printed no loudness summary"):
        measure(video, ffmpeg=str(ffmpeg.path))


def test_ffmpeg_is_the_one_in_ffmpeg_bin_when_not_given(monkeypatch, ffmpeg, video):
    ffmpeg.measures(summary(-14.0, -1.4))
    monkeypatch.setenv("FFMPEG_BIN", str(ffmpeg.path))

    assert measure(video).integrated_lufs == -14.0
    assert mastering.ffmpeg_binary() == str(ffmpeg.path)
