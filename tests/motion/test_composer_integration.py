"""Opt-in: compose a small shot list and render it with the real pinned HyperFrames and a real browser.

Skipped unless ``HYPERFRAMES_MCP_RENDER_INTEGRATION=1`` (see ``test_render_integration.py`` for what it needs: ``hyperframes-mcp setup``,
a chrome-headless-shell and ffmpeg-full). It makes
no network call and no paid call; the voice, the music bed and the sound effect are made here with ffmpeg:

    HYPERFRAMES_MCP_RENDER_INTEGRATION=1 pytest tests/motion/test_composer_integration.py

The shot list is the first two shots of the sample, 12 s: an act title with a lower third, a dissolve into a ledger with
a second lower third (two mounts of one block, one of them partly overlapping the other). The voice is silent so that
the music duck can be measured on the render's own sound.

The second test renders two ``video-shot`` mounts of one 6 s clip (a frame counter and a barcode of the frame number,
made here with ffmpeg) and reads the barcode of every frame of the render.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from hyperframes_mcp.hfcli.cli import NODE_DIR
from hyperframes_mcp.motion import render, sheet, tokens
from hyperframes_mcp.motion.compose import compose
from hyperframes_mcp.motion.master import master, measure
from hyperframes_mcp.motion.shotlist import parse_shotlist
from hyperframes_mcp.motion.timing import Word
from tests.motion.composer_helpers import sample_shotlist

pytestmark = pytest.mark.skipif(
    os.environ.get("HYPERFRAMES_MCP_RENDER_INTEGRATION") != "1",
    reason="set HYPERFRAMES_MCP_RENDER_INTEGRATION=1 to render with the real HyperFrames",
)

NODE = NODE_DIR
LENGTH = 12.0
# where the voice "speaks" (the voice file is silent): the music is ducked from just before 3 s to just after 7 s
SPEECH = [Word("one", 3.0, 4.0), Word("two", 4.2, 5.4), Word("three", 5.6, 7.0)]
DUCK_DB = -9.0
# frames to look at: the act title entering, its hold with the first lower third, mid-dissolve, the ledger with the second
MOMENTS = [0.6, 3.0, 5.75, 9.0, 11.0]


def shot_list() -> dict:
    raw = sample_shotlist()
    del raw["shots"][2]
    raw["audio"]["sfx"][0]["at"] = 9.5
    raw["audio"]["music"]["duck_under_voice_db"] = DUCK_DB
    return raw


def make_audio(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    ffmpeg = sheet.ffmpeg_binary()
    sources = {
        "voice.wav": f"anullsrc=r=48000:cl=stereo:d={LENGTH}",
        "bed.wav": f"sine=frequency=220:sample_rate=48000:duration={LENGTH}",
        "whoosh.wav": "anoisesrc=d=0.4:r=48000:amplitude=0.3",
    }
    for name, source in sources.items():
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                source,
                "-ac",
                "2",
                str(folder / name),
            ],
            check=True,
        )


def mean_volume(video: Path, start: float, length: float) -> float:
    done = subprocess.run(
        [
            sheet.ffmpeg_binary(),
            "-hide_banner",
            "-ss",
            str(start),
            "-t",
            str(length),
            "-i",
            str(video),
            "-vn",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(re.search(r"mean_volume: (-?[\d.]+) dB", done.stderr).group(1))


def lit_share(frame: Path, box: tuple[int, int, int, int]) -> float:
    """The share of pixels in ``box`` that differ from the night ground (a band of paper, at any opacity)."""
    night = tokens.default_tokens().colors["night"]
    ground = tuple(int(night[i : i + 2], 16) for i in (1, 3, 5))
    pixels = list(Image.open(frame).convert("RGB").crop(box).getdata())
    lit = sum(1 for p in pixels if max(abs(a - b) for a, b in zip(p, ground, strict=True)) > 12)
    return lit / len(pixels)


def test_a_shot_list_composes_renders_and_masters(tmp_path):
    assets = tmp_path / "assets"
    make_audio(assets / "audio")
    result = compose(
        parse_shotlist(shot_list()),
        SPEECH,
        LENGTH,
        tmp_path / "project",
        assets_dir=assets,
    )

    first = render.render_project(result.project_dir, tmp_path / "a.mp4", node_dir=NODE)

    assert (first.width, first.height, first.fps) == (1920, 1080, 24)
    assert first.duration_s == pytest.approx(result.total_s, abs=0.5)
    assert result.total_s == LENGTH

    # the lower third of the first shot sits bottom left in its hold, the second one bottom right in the second shot's:
    # two mounts of the same block, each with its own timeline, both drawn (the ledger's sheet is in the middle)
    frames = sheet.extract_frames(first.path, MOMENTS, tmp_path / "frames")
    left, right = (96, 800, 760, 1030), (1160, 800, 1824, 1030)
    shares = {at: (lit_share(frame, left), lit_share(frame, right)) for at, frame in zip(MOMENTS, frames, strict=True)}
    # entering: the first band is already wiping in, nothing at the right yet
    assert shares[0.6][0] > 0.1 and shares[0.6][1] < 0.02
    # the first lower third, held
    assert shares[3.0][0] > 0.4 and shares[3.0][1] < 0.02
    # mid-dissolve both are on screen, the second still wiping in at the right
    assert shares[5.75][0] > 0.2 and shares[5.75][1] > 0.02
    # the second lower third, held, the first gone
    assert shares[9.0][1] > 0.4 and shares[9.0][0] < 0.02
    night = tokens.default_tokens().colors["night"]
    corner = Image.open(frames[0]).convert("RGB").getpixel((1900, 20))
    assert all(abs(got - int(night[i : i + 2], 16)) <= 3 for got, i in zip(corner, (1, 3, 5), strict=True))

    # the music is ducked while the voice speaks: the same tone, 9 dB lower between 3 s and 7 s than before it
    clear, ducked = mean_volume(first.path, 0.5, 2.0), mean_volume(first.path, 4.5, 2.0)
    assert clear - ducked == pytest.approx(-DUCK_DB, abs=1.5)

    second = render.render_project(result.project_dir, tmp_path / "b.mp4", node_dir=NODE)
    assert second.sha256 == first.sha256

    done = master(first.path, tmp_path / "final.mp4", lufs=-14.0, true_peak_db=-1.0)
    assert done.integrated_lufs == pytest.approx(-14.0, abs=0.5)
    assert done.true_peak_dbtp <= -1.0
    assert measure(tmp_path / "final.mp4") == done


# -- two video-shot mounts of one clip, frame by frame ---------------------------------------------

CLIP_FRAMES = 144  # a 6 s clip at 24 fps
BARCODE = (
    1160,
    40,
    720,
    80,
)  # x, y, width, height of the strip of eight 80 px squares, one per bit of the frame number


def make_clip(path: Path) -> None:
    """A 6 s 1920x1080 24 fps clip: test pattern, 'frame N', a barcode of N, and a 440 Hz tone as its own sound."""
    path.parent.mkdir(parents=True, exist_ok=True)
    chain = [
        "testsrc2=size=1920x1080:rate=24:duration=6",
        "drawtext=text='frame %{frame_num}':x=60:y=60:fontsize=120:fontcolor=white:box=1:boxcolor=black@0.7:boxborderw=14",
    ]
    for bit in range(8):
        x = 1920 - 40 - (8 - bit) * 90
        chain.append(f"drawbox=x={x}:y=40:w=80:h=80:color=black:t=fill")
        chain.append(f"drawbox=x={x}:y=40:w=80:h=80:color=white:t=fill:enable='gte(mod(floor(n/pow(2,{bit})),2),1)'")
    subprocess.run(
        [
            sheet.ffmpeg_binary(), "-v", "error", "-y",
            "-f", "lavfi", "-i", ",".join(chain),
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-g", "24",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )  # fmt: skip


def frame_numbers(video: Path) -> list[int]:
    """The source frame each frame of ``video`` shows, read from the barcode in its top right corner."""
    x, y, width, height = BARCODE
    raw = subprocess.run(
        [
            sheet.ffmpeg_binary(), "-v", "error", "-i", str(video),
            "-vf", f"crop={width}:{height}:{x}:{y},format=gray", "-f", "rawvideo", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout  # fmt: skip
    strips = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width)
    numbers = []
    for strip in strips:
        value = 0
        for bit in range(8):
            left = 1920 - 40 - (8 - bit) * 90 - x
            if strip[10:70, left + 10 : left + 70].mean() > 127:
                value |= 1 << bit
        numbers.append(value)
    return numbers


def clip_shot_list() -> dict:
    grade = '{"exposure":0,"contrast":0.05,"temperature":0.05}'

    def shot(shot_id, start, trim, hold):
        return {
            "id": shot_id,
            "start": start,
            "duration": 9,
            "layers": [
                {
                    "component": "video-shot",
                    "at": 0,
                    "variables": {
                        "video": "video/clip.mp4",
                        "trimStart": trim,
                        "grade": grade,
                        "scrim": "bottom",
                        "hold": hold,
                    },
                },
                {
                    "component": "credit-line",
                    "at": 0.5,
                    "duration": 6,
                    "variables": {"text": "AI-generated illustration", "align": "left"},
                },
            ],
        }

    return {
        "version": 1,
        "meta": {"title": "video-shot", "language": "en", "publish": True},
        "audio": {"voice": {"path": "audio/voice.wav"}},
        "shots": [shot("freeze", 0, 0, "freeze"), shot("loop", 9, 2, "loop")],
    }


def test_a_video_shot_plays_trims_freezes_loops_and_is_silent_frame_by_frame(tmp_path):
    assets = tmp_path / "assets"
    make_clip(assets / "video" / "clip.mp4")
    (assets / "audio").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sheet.ffmpeg_binary(), "-v", "error", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=stereo:d=18", str(assets / "audio" / "voice.wav"),
        ],
        check=True,
    )  # fmt: skip
    result = compose(
        parse_shotlist(clip_shot_list()),
        [],
        18.0,
        tmp_path / "project",
        assets_dir=assets,
        clip_seconds=lambda path: 6.0,
    )

    first = render.render_project(result.project_dir, tmp_path / "a.mp4", node_dir=NODE)

    assert (first.width, first.height, first.fps) == (1920, 1080, 24)
    assert first.duration_s == pytest.approx(18.0, abs=0.5)
    # every frame shows the source frame it should: shot 1 plays the clip once and holds its last frame (143) for the
    # rest of its 9 s; shot 2 starts at frame 48 (trimStart 2 s) and starts that part again each time it ends
    expected = [min(n, CLIP_FRAMES - 1) for n in range(216)] + [48 + (n % (CLIP_FRAMES - 48)) for n in range(216)]
    assert frame_numbers(first.path) == expected

    # the grade and the finish are on the clip: grain on a flat colour bar, the vignette at the top edge, the scrim at the
    # bottom (a frame of the render against the same frame of the clip)
    [rendered] = sheet.extract_frames(first.path, [5.958], tmp_path / "shown")
    [source] = sheet.extract_frames(assets / "video" / "clip.mp4", [5.958], tmp_path / "plain")
    out, src = (np.asarray(Image.open(p).convert("L")).astype(float) for p in (rendered, source))
    assert src[300:360, 100:160].std() == 0 and out[300:360, 100:160].std() > 0.2
    assert out[1010:1070, 100:160].mean() < 0.8 * src[1010:1070, 100:160].mean()
    assert out[0:60, 720:780].mean() < 0.97 * src[0:60, 720:780].mean()

    # only the voice is heard (silent here): the clip's own 440 Hz tone is not in the sound
    assert mean_volume(first.path, 0, 18) < -60

    second = render.render_project(result.project_dir, tmp_path / "b.mp4", node_dir=NODE)
    assert second.sha256 == first.sha256
