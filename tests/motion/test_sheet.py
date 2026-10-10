"""Frames and the contact sheet: which ffmpeg, which moments, what the sheet looks like."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from hyperframes_mcp.motion import sheet, tokens
from tests.motion.render_fakes import fake_toolchain, ffmpeg_calls


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))


def swatch(path: Path, colour: tuple[int, int, int], size=(480, 270)) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


# -- which ffmpeg --------------------------------------------------------------------------------


def test_ffmpeg_is_the_one_the_environment_names(monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "/somewhere/ffmpeg")

    assert sheet.ffmpeg_binary() == "/somewhere/ffmpeg"
    assert sheet.media_environment()["FFMPEG_BIN"] == "/somewhere/ffmpeg"


def test_without_the_variable_it_is_the_project_ffmpeg_when_installed(monkeypatch, tmp_path):
    installed = tmp_path / "ffmpeg"
    installed.write_text("")
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.setattr(sheet, "DEFAULT_FFMPEG", str(installed))

    assert sheet.ffmpeg_binary() == str(installed)
    assert sheet.media_environment()["FFMPEG_BIN"] == str(installed)


def test_without_either_it_is_ffmpeg_on_the_path(monkeypatch, tmp_path):
    monkeypatch.delenv("FFMPEG_BIN", raising=False)
    monkeypatch.setattr(sheet, "DEFAULT_FFMPEG", str(tmp_path / "absent"))

    assert sheet.ffmpeg_binary() == "ffmpeg"


# -- which moments -------------------------------------------------------------------------------


def test_the_frames_are_the_start_the_end_of_the_fade_in_the_middle_and_the_start_of_the_fade_out():
    duration = tokens.default_tokens().duration

    moments = sheet.review_times(6.2)

    assert [label for label, _ in moments] == [
        "start",
        "fade-in end",
        "50%",
        "fade-out start",
    ]
    assert [at for _, at in moments] == pytest.approx([0.0, duration["enter"], 3.1, 6.2 - duration["exit"]])
    assert duration["enter"] == 0.7 and duration["exit"] == 0.5


def test_a_start_offset_shifts_every_moment_into_the_video():
    moments = sheet.review_times(8.0, start=2.0)

    assert [at for _, at in moments] == pytest.approx([2.0, 2.7, 6.0, 9.5])


# -- extracting frames ---------------------------------------------------------------------------


def clip(tmp_path: Path) -> Path:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")
    return video


def test_each_time_gets_one_frame_in_order_with_ffmpeg_seeking_to_it(tmp_path):
    tools = fake_toolchain(tmp_path / "bin")
    video = clip(tmp_path)

    frames = sheet.extract_frames(video, [0.0, 0.7, 3.1], tmp_path / "frames", ffmpeg=tools.media.ffmpeg)

    assert [f.name for f in frames] == [
        "clip-frame-00-0.00s.png",
        "clip-frame-01-0.70s.png",
        "clip-frame-02-3.10s.png",
    ]
    assert all(f.is_file() and f.parent == tmp_path / "frames" for f in frames)
    calls = ffmpeg_calls(tmp_path / "bin")
    assert [c[c.index("-ss") + 1] for c in calls] == ["0.000", "0.700", "3.100"]
    assert all(c[c.index("-i") + 1] == str(video.resolve()) and "-frames:v" in c for c in calls)
    # only a plain file is ever opened as input, never a protocol such as concat:
    assert all(c[c.index("-protocol_whitelist") + 1] == "file" for c in calls)


def test_ffmpeg_failing_is_an_error_with_its_message(tmp_path):
    failing = tmp_path / "ffmpeg"
    failing.write_text('#!/bin/sh\necho "moov atom not found" >&2\nexit 1\n')
    failing.chmod(0o755)

    with pytest.raises(sheet.SheetError, match="no frame at 0.70 s.*moov atom not found"):
        sheet.extract_frames(clip(tmp_path), [0.7], tmp_path / "frames", ffmpeg=str(failing))


def test_a_time_past_the_end_writes_no_frame_and_is_an_error(tmp_path):
    silent = tmp_path / "ffmpeg"
    silent.write_text("#!/bin/sh\nexit 0\n")
    silent.chmod(0o755)

    with pytest.raises(sheet.SheetError, match="no frame was written"):
        sheet.extract_frames(clip(tmp_path), [99.0], tmp_path / "frames", ffmpeg=str(silent))


def test_a_missing_ffmpeg_is_an_error(tmp_path):
    with pytest.raises(sheet.SheetError, match="did not run"):
        sheet.extract_frames(
            clip(tmp_path),
            [0.0],
            tmp_path / "frames",
            ffmpeg=str(tmp_path / "absent" / "ffmpeg"),
        )


@pytest.mark.parametrize("name", ["missing.mp4", "notes.txt", "concat:a|b.mp4"])
def test_only_a_plain_existing_video_file_reaches_ffmpeg(tmp_path, name):
    if name == "notes.txt":
        (tmp_path / name).write_text("x")
    tools = fake_toolchain(tmp_path / "bin")

    with pytest.raises(sheet.SheetError):
        sheet.extract_frames(tmp_path / name, [0.0], tmp_path / "frames", ffmpeg=tools.media.ffmpeg)

    assert ffmpeg_calls(tmp_path / "bin") == []


def test_the_review_is_four_frames_and_a_sheet_beside_the_video(tmp_path, monkeypatch):
    tools = fake_toolchain(tmp_path / "bin")
    monkeypatch.setenv("FFMPEG_BIN", tools.media.ffmpeg)
    video = clip(tmp_path)

    frames, target = sheet.review_sheet(video, 8.0, start=1.0)

    assert [f.name for f in frames] == [
        "clip-frame-00-1.00s.png",
        "clip-frame-01-1.70s.png",
        "clip-frame-02-5.00s.png",
        "clip-frame-03-8.50s.png",
    ]
    assert frames[0].parent == tmp_path / "clip.frames"
    assert target == tmp_path / "clip.sheet.png" and target.is_file()


# -- the contact sheet ---------------------------------------------------------------------------


def four_frames(tmp_path):
    colours = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (200, 200, 30)]
    return [(f"frame {i}  {i * 1.5:.2f} s", swatch(tmp_path / f"f{i}.png", c)) for i, c in enumerate(colours)], colours


def test_the_sheet_is_a_grid_of_tiles_with_a_label_under_each(tmp_path):
    frames, colours = four_frames(tmp_path)

    target = sheet.build_sheet(frames, tmp_path / "out" / "sheet.png")

    image = Image.open(target).convert("RGB")
    gap, label, tile_w, tile_h = 24, 64, 960, 540
    assert image.size == (
        gap + 2 * (tile_w + gap),
        gap + 2 * (tile_h + label + gap),
    )
    for index, colour in enumerate(colours):
        left = gap + (index % 2) * (tile_w + gap)
        top = gap + (index // 2) * (tile_h + label + gap)
        assert image.getpixel((left + tile_w // 2, top + tile_h // 2)) == colour
        # the label strip under the tile holds text in paper on the dark scrim
        strip = image.crop((left, top + tile_h, left + tile_w, top + tile_h + label))
        assert max(sum(px) for px in strip.getdata()) > 600, "no label text was drawn"


def test_the_background_and_label_colours_are_the_tokens(tmp_path):
    frames, _ = four_frames(tmp_path)
    colors = tokens.default_tokens().colors

    image = Image.open(sheet.build_sheet(frames, tmp_path / "sheet.png")).convert("RGB")

    assert image.getpixel((2, 2)) == hex_to_rgb(colors["scrim"])
    assert hex_to_rgb(colors["paper"]) in {px for px in image.getdata()}


def test_three_frames_make_two_rows_and_one_frame_makes_one_cell(tmp_path):
    frames, _ = four_frames(tmp_path)

    three = Image.open(sheet.build_sheet(frames[:3], tmp_path / "three.png"))
    one = Image.open(sheet.build_sheet(frames[:1], tmp_path / "one.png"))

    assert three.size[0] == 24 + 2 * (960 + 24) and three.size[1] == 24 + 2 * (540 + 64 + 24)
    assert one.size == (24 + 960 + 24, 24 + 540 + 64 + 24)


def test_a_narrower_tile_keeps_the_aspect_ratio(tmp_path):
    frames, _ = four_frames(tmp_path)

    image = Image.open(sheet.build_sheet(frames, tmp_path / "small.png", columns=4, tile_width=480))

    assert image.size == (24 + 4 * (480 + 24), 24 + 270 + 64 + 24)


def test_no_frames_is_an_error(tmp_path):
    with pytest.raises(sheet.SheetError, match="at least one frame"):
        sheet.build_sheet([], tmp_path / "sheet.png")


def test_an_unreadable_frame_is_an_error_naming_it(tmp_path):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")

    with pytest.raises(sheet.SheetError, match="broken.png"):
        sheet.build_sheet([("x", broken)], tmp_path / "sheet.png")
