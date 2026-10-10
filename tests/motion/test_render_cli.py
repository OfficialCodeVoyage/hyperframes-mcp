"""``python -m hyperframes_mcp.motion render``: stage, render, look, compare; with a fake CLI, ffprobe and ffmpeg."""

from __future__ import annotations

import json
import re

import pytest
from PIL import Image

from hyperframes_mcp.hfcli.settings import AnimatedSettings
from hyperframes_mcp.motion import __main__ as cli
from hyperframes_mcp.motion import render
from tests.motion.render_fakes import (
    WRITE_CLIP,
    WRITE_VARYING_CLIP,
    fake_toolchain,
    ffmpeg_calls,
    probe_json,
)

SEE_THE_PAGE = 'cp index.html "$(dirname "$0")/index-seen.html"\n'


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """The fake toolchain in ``tmp_path/bin``; ``setup(**kwargs)`` rebuilds it with another CLI body or probe."""
    folder = tmp_path / "bin"

    def setup(**kwargs):
        tools = fake_toolchain(folder, **kwargs)
        monkeypatch.setattr(render, "find_toolchain", lambda *a, **k: tools)
        monkeypatch.setenv("FFMPEG_BIN", tools.media.ffmpeg)

    monkeypatch.setattr(
        render,
        "load_animated_settings",
        lambda: AnimatedSettings(network_isolation="off"),
    )
    setup()
    setup.folder = folder
    return setup


def run(capsys, *argv):
    code = cli.main(["render", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_render_writes_the_mp4_and_reports_it(fakes, capsys, tmp_path):
    out = tmp_path / "out" / "credit.mp4"

    code, stdout, _ = run(capsys, "credit-line", "--out", str(out))

    assert code == 0 and out.read_text() == "frames\n"
    assert f"rendered {out}: 1920x1080 24 fps h264, 6.20 s" in stdout
    assert re.search(r"^sha256 [0-9a-f]{64}$", stdout, re.MULTILINE)
    assert "sheet" not in stdout and not out.with_suffix(".sheet.png").exists()
    assert ffmpeg_calls(fakes.folder) == []


def test_without_out_the_mp4_goes_to_the_output_folder_named_after_the_block(
    fakes, capsys, tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "DEFAULT_OUT_DIR", tmp_path / "output" / "motion")

    code, _, _ = run(capsys, "credit-line")

    assert code == 0
    assert (tmp_path / "output" / "motion" / "credit-line.mp4").is_file()


def test_the_default_output_folder_is_output_motion_in_the_current_folder():
    assert cli.DEFAULT_OUT_DIR.parts[-2:] == ("output", "motion")


def test_sheet_extracts_the_four_moments_and_writes_the_contact_sheet_beside_the_mp4(
    fakes, capsys, tmp_path
):
    out = tmp_path / "credit.mp4"

    code, stdout, _ = run(capsys, "credit-line", "--out", str(out), "--sheet")

    assert code == 0
    calls = ffmpeg_calls(fakes.folder)
    assert [c[c.index("-ss") + 1] for c in calls] == [
        "0.000",
        "0.700",
        "3.100",
        "5.700",
    ]
    assert sorted(p.name for p in (tmp_path / "credit.frames").iterdir()) == [
        "credit-frame-00-0.00s.png",
        "credit-frame-01-0.70s.png",
        "credit-frame-02-3.10s.png",
        "credit-frame-03-5.70s.png",
    ]
    sheet = tmp_path / "credit.sheet.png"
    assert Image.open(sheet).size[0] > 1900
    assert (
        f"sheet {sheet}" in stdout and f"frames {tmp_path / 'credit.frames'}" in stdout
    )


def test_vars_reach_the_block_and_set_the_frame_times(fakes, capsys, tmp_path):
    fakes(cli_body=SEE_THE_PAGE + WRITE_CLIP, probe=probe_json(8.0))
    variables = {"text": "Bank of England", "durationSeconds": 8, "align": "right"}

    code, _, _ = run(
        capsys,
        "credit-line",
        "--out",
        str(tmp_path / "c.mp4"),
        "--vars",
        json.dumps(variables),
        "--sheet",
    )

    assert code == 0
    page = (fakes.folder / "index-seen.html").read_text(encoding="utf-8")
    assert "Bank of England" in page and "&quot;align&quot;: &quot;right&quot;" in page
    times = [c[c.index("-ss") + 1] for c in ffmpeg_calls(fakes.folder)]
    assert times == ["0.000", "0.700", "4.000", "7.500"]


def test_twice_reports_identical_renders_and_exits_0(fakes, capsys, tmp_path):
    code, stdout, _ = run(
        capsys, "credit-line", "--out", str(tmp_path / "c.mp4"), "--twice"
    )

    assert code == 0
    assert re.search(r"^twice: identical, sha256 [0-9a-f]{64}$", stdout, re.MULTILINE)
    assert not (tmp_path / "again.mp4").exists()  # the second render is scratch


def test_twice_reports_different_renders_and_exits_1(fakes, capsys, tmp_path):
    fakes(cli_body=WRITE_VARYING_CLIP)

    code, stdout, _ = run(
        capsys, "credit-line", "--out", str(tmp_path / "c.mp4"), "--twice"
    )

    assert code == 1
    assert "twice: DIFFERENT" in stdout


def test_a_failing_render_exits_1_with_the_reason_on_stderr(fakes, capsys, tmp_path):
    fakes(cli_body='echo "chrome crashed" >&2\nexit 3\n')

    code, stdout, err = run(capsys, "credit-line", "--out", str(tmp_path / "c.mp4"))

    assert code == 1 and stdout == ""
    assert err.startswith("error: renderer exited with status 3: chrome crashed")


def test_a_clip_that_is_not_accepted_exits_1(fakes, capsys, tmp_path):
    fakes(probe=probe_json(fps="30/1"))

    code, _, err = run(capsys, "credit-line", "--out", str(tmp_path / "c.mp4"))

    assert code == 1 and "expected 24 fps" in err


def test_a_frame_that_cannot_be_extracted_exits_1(fakes, capsys, tmp_path, monkeypatch):
    broken = tmp_path / "broken-ffmpeg"
    broken.write_text('#!/bin/sh\necho "no such frame" >&2\nexit 1\n')
    broken.chmod(0o755)
    monkeypatch.setenv("FFMPEG_BIN", str(broken))

    code, _, err = run(
        capsys, "credit-line", "--out", str(tmp_path / "c.mp4"), "--sheet"
    )

    assert code == 1 and "no frame at 0.00 s" in err and "no such frame" in err


def test_the_toolchain_missing_exits_1_and_says_so(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(
        render,
        "load_animated_settings",
        lambda: AnimatedSettings(network_isolation="off"),
    )

    code, _, err = run(
        capsys,
        "credit-line",
        "--out",
        str(tmp_path / "c.mp4"),
        "--node-dir",
        str(tmp_path / "no-scene-render"),
    )

    assert code == 1 and err.startswith("error: ")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["credit-line", "--vars", "{nope"], "--vars is not valid JSON"),
        (["credit-line", "--vars", "[1, 2]"], "--vars must be a JSON object"),
        (["credit-line", "--vars", '{"txt": "typo"}'], "has no variable txt"),
        (["credit-line", "--vars", '{"durationSeconds": 0}'], "positive number"),
        (["nope"], "available: "),
    ],
)
def test_a_bad_argument_exits_2_before_any_render(
    fakes, capsys, tmp_path, argv, message
):
    code, stdout, err = run(capsys, *argv, "--out", str(tmp_path / "c.mp4"))

    assert code == 2 and message in err and stdout == ""
    assert not (tmp_path / "c.mp4").exists()


def test_the_node_dir_defaults_to_the_folder_setup_installs():
    args = cli.build_parser().parse_args(["render", "credit-line"])

    assert args.node_dir == cli.NODE_DIR
    assert (args.vars, args.out, args.sheet, args.twice) == ("", None, False, False)
