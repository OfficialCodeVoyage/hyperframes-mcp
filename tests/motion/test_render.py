"""``render_project``: the hardened run and the acceptance of the clip, with a fake CLI, fake ffprobe and no Chrome.

The hardening itself (scrubbed environment, process-group kill, the sandbox command) is shared with the scene
renderer and tested in ``tests/visual_generator/animated/test_renderer.py``; here the wiring is checked: what
the motion render asks of it and what it refuses to accept.
"""

from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

import pytest

from hyperframes_mcp.hfcli import cli as cli_module
from hyperframes_mcp.hfcli.cli import (
    RendererUnavailable,
    RenderFailed,
    RenderTimeout,
)
from hyperframes_mcp.hfcli.settings import AnimatedSettings
from hyperframes_mcp.motion import render
from hyperframes_mcp.motion.stage import stage_block
from tests.motion.render_fakes import (
    TAKE_OUTPUT,
    WRITE_CLIP,
    fake_toolchain,
    probe_json,
)

SETTINGS = AnimatedSettings(network_isolation="off")


@pytest.fixture
def staged(tmp_path):
    return stage_block("credit-line", None, tmp_path / "project")


def use(monkeypatch, tmp_path, **kwargs):
    """Make ``render_project`` find the fake toolchain; returns the folder the fakes log into."""
    folder = tmp_path / "bin"
    tools = fake_toolchain(folder, **kwargs)
    monkeypatch.setattr(render, "find_toolchain", lambda *a, **k: tools)
    return folder


# -- what the project asks for -------------------------------------------------------------------


def test_the_expected_length_is_the_root_compositions_duration(tmp_path):
    stage_block("credit-line", {"durationSeconds": 9.5}, tmp_path)

    assert render.project_duration(tmp_path) == 9.5


def test_a_project_without_an_index_or_a_root_duration_cannot_be_rendered(tmp_path):
    with pytest.raises(RenderFailed, match="no index.html"):
        render.project_duration(tmp_path)

    (tmp_path / "index.html").write_text("<html><body></body></html>")
    with pytest.raises(RenderFailed, match="no root composition"):
        render.project_duration(tmp_path)


# -- a good render -------------------------------------------------------------------------------


def test_a_render_returns_the_clip_and_its_measurements(monkeypatch, tmp_path, staged):
    use(monkeypatch, tmp_path)
    target = tmp_path / "out" / "deep" / "clip.mp4"

    result = render.render_project(staged.project_dir, target, settings=SETTINGS)

    assert result.path == target and target.read_text() == "frames\n"
    assert (result.width, result.height, result.fps) == (1920, 1080, 24)
    assert result.duration_s == 6.2 and result.size_bytes == len("frames\n")
    assert result.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert result.isolation == "environment"
    assert list(target.parent.iterdir()) == [target]  # no half-written file beside it


def test_the_cli_is_run_in_the_project_at_24_fps_with_the_output_asked_for(monkeypatch, tmp_path, staged):
    folder = use(
        monkeypatch,
        tmp_path,
        cli_body='echo "$@" > "$(dirname "$0")/argv.txt"\npwd > "$(dirname "$0")/cwd.txt"\nls >> "$(dirname "$0")/cwd.txt"\n'
        + WRITE_CLIP,
    )

    render.render_project(staged.project_dir, tmp_path / "clip.mp4", settings=SETTINGS)

    argv = (folder / "argv.txt").read_text().split()
    assert argv[0] == "render" and "--quiet" in argv
    assert "--no-best-effort" in argv
    assert argv[argv.index("--fps") + 1] == "24"
    assert argv[argv.index("--quality") + 1] == "standard"
    assert argv[argv.index("--workers") + 1] == str(SETTINGS.workers)
    # the clip is written to a scratch path first and only put at the one asked for once it is accepted
    assert argv[argv.index("--output") + 1] != str(tmp_path / "clip.mp4")
    cwd, *listing = (folder / "cwd.txt").read_text().split()
    assert Path(cwd).resolve() == staged.project_dir.resolve()
    assert {"index.html", "compositions", "vendor"} <= set(listing)


def test_the_child_gets_no_secrets_and_a_private_home(monkeypatch, tmp_path, staged):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    folder = use(
        monkeypatch,
        tmp_path,
        cli_body='env > "$(dirname "$0")/env.txt"\n' + WRITE_CLIP,
    )

    render.render_project(staged.project_dir, tmp_path / "clip.mp4", settings=SETTINGS)

    dump = (folder / "env.txt").read_text()
    assert "sk-secret" not in dump
    assert "HYPERFRAMES_NO_TELEMETRY=1" in dump and "DO_NOT_TRACK=1" in dump
    assert "motion-render-" in dump


# -- what is refused -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("probe", "message"),
    [
        (probe_json(codec="vp9"), "expected h264 1920x1080"),
        (probe_json(width=1280, height=720), "expected h264 1920x1080"),
        (probe_json(fps="30/1"), "runs at 30 fps; expected 24 fps"),
        (probe_json(fps="0/0"), "unknown rate"),
        (probe_json(duration=3.0), "3.00 s long; the project asks for 6.2 s"),
        (probe_json(duration=7.0), "7.00 s long; the project asks for 6.2 s"),
        (
            {"streams": [{"codec_type": "audio"}], "format": {"duration": "6.2"}},
            "no video stream",
        ),
    ],
)
def test_a_clip_that_is_not_what_the_project_asked_for_is_rejected(monkeypatch, tmp_path, staged, probe, message):
    use(monkeypatch, tmp_path, probe=probe)
    target = tmp_path / "clip.mp4"

    with pytest.raises(RenderFailed, match=message):
        render.render_project(staged.project_dir, target, settings=SETTINGS)

    assert not target.exists()


def test_half_a_second_either_way_is_within_the_slack(monkeypatch, tmp_path, staged):
    use(monkeypatch, tmp_path, probe=probe_json(duration=6.65))

    result = render.render_project(staged.project_dir, tmp_path / "clip.mp4", settings=SETTINGS)

    assert result.duration_s == 6.65


def test_a_clip_over_the_size_limit_is_rejected_before_it_is_probed(monkeypatch, tmp_path, staged):
    use(
        monkeypatch,
        tmp_path,
        cli_body=TAKE_OUTPUT + 'head -c 2000000 /dev/zero > "$out"\n',
    )
    monkeypatch.setattr(cli_module, "probe_media", lambda *a: pytest.fail("probed an oversized clip"))

    with pytest.raises(RenderFailed, match=r"the clip is 2\.0 MB; the limit is 1\.0 MB"):
        render.render_project(
            staged.project_dir,
            tmp_path / "clip.mp4",
            settings=AnimatedSettings(network_isolation="off", max_output_mb=1),
        )


def test_a_failing_cli_is_a_render_failure_with_the_tail_of_its_output(monkeypatch, tmp_path, staged):
    use(
        monkeypatch,
        tmp_path,
        cli_body='echo "chrome crashed: out of memory" >&2\nexit 3\n',
    )

    with pytest.raises(RenderFailed, match=r"status 3: chrome crashed: out of memory"):
        render.render_project(staged.project_dir, tmp_path / "clip.mp4", settings=SETTINGS)

    assert not (tmp_path / "clip.mp4").exists()


# what the real CLI does with credit-line given a slot too short to read: the page prints the block's own
# error, and best-effort mode (its default) writes empty frames and exits 0, strict mode exits 1
THROWS = (
    'echo "[Browser:ERROR] credit-line threw: credit-line: '
    'durationSeconds 8 is shorter than the 9.90 s it needs (fade in, reading time, fade out)"\n'
    'echo "[WARN] Render completed capture with correctness warnings sub_timeline_readiness_timeout"\n'
    'strict=no; for arg in "$@"; do [ "$arg" = "--no-best-effort" ] && strict=yes; done\n'
    'if [ "$strict" = yes ]; then echo "Render failed" >&2; exit 1; fi\n'
)


def test_a_block_that_threw_fails_the_render_and_says_why_instead_of_writing_empty_frames(
    monkeypatch, tmp_path, staged
):
    use(monkeypatch, tmp_path, cli_body=THROWS + WRITE_CLIP)
    target = tmp_path / "clip.mp4"

    with pytest.raises(RenderFailed) as raised:
        render.render_project(staged.project_dir, target, settings=SETTINGS)

    assert str(raised.value).startswith(
        "renderer exited with status 1: credit-line threw: credit-line: durationSeconds 8"
    )
    assert "durationSeconds 8 is shorter than the 9.90 s it needs" in str(raised.value)
    assert not target.exists()


def test_a_cli_that_writes_no_clip_is_a_failure(monkeypatch, tmp_path, staged):
    use(monkeypatch, tmp_path, cli_body="exit 0\n")

    with pytest.raises(RenderFailed, match="without writing a clip"):
        render.render_project(staged.project_dir, tmp_path / "clip.mp4", settings=SETTINGS)


def test_a_render_that_overruns_the_timeout_is_killed(monkeypatch, tmp_path, staged):
    use(monkeypatch, tmp_path, cli_body="sleep 60\n")

    with pytest.raises(RenderTimeout, match="exceeded"):
        render.render_project(staged.project_dir, tmp_path / "clip.mp4", timeout=0.5, settings=SETTINGS)


def test_the_default_timeout_is_the_settings_floor_or_a_multiple_of_the_project(monkeypatch, tmp_path, staged):
    seen = []

    def stop_at_the_cli(*args, **kwargs):
        seen.append(kwargs["timeout"])
        raise RenderFailed("stop")

    monkeypatch.setattr(render, "run_cli", stop_at_the_cli)
    use(monkeypatch, tmp_path)
    settings = AnimatedSettings(network_isolation="off", render_timeout_floor_s=60, render_timeout_factor=6)

    with pytest.raises(RenderFailed, match="stop"):
        render.render_project(staged.project_dir, tmp_path / "a.mp4", settings=settings)
    with pytest.raises(RenderFailed, match="stop"):
        render.render_project(staged.project_dir, tmp_path / "b.mp4", timeout=5, settings=settings)

    assert seen == [
        60,
        5,
    ]  # 6 x 6.2 = 37.2 s is under the floor; an explicit timeout wins


# -- not available -------------------------------------------------------------------------------


def test_a_missing_toolchain_is_unavailable_not_a_crash(tmp_path, staged):
    with pytest.raises(RendererUnavailable):
        render.render_project(
            staged.project_dir,
            tmp_path / "clip.mp4",
            node_dir=tmp_path / "no-scene-render",
            settings=SETTINGS,
        )


def test_requiring_a_sandbox_where_there_is_none_is_unavailable(monkeypatch, tmp_path, staged):
    use(monkeypatch, tmp_path)
    monkeypatch.setattr(cli_module, "sys", types.SimpleNamespace(platform="linux"))

    with pytest.raises(RendererUnavailable, match="no network sandbox"):
        render.render_project(
            staged.project_dir,
            tmp_path / "clip.mp4",
            settings=AnimatedSettings(network_isolation="require"),
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS only")
def test_on_macos_the_default_runs_the_cli_under_the_no_network_sandbox(monkeypatch, tmp_path, staged):
    use(monkeypatch, tmp_path)

    result = render.render_project(
        staged.project_dir,
        tmp_path / "clip.mp4",
        settings=AnimatedSettings(network_isolation="auto"),
    )

    assert result.isolation == "sandbox"


def test_the_size_cap_grows_with_the_length_of_a_video_and_never_shrinks(monkeypatch):
    monkeypatch.setattr(render, "load_animated_settings", lambda: AnimatedSettings(max_output_mb=40))

    assert render.settings_for_length(12).max_output_mb == 40
    # an 87 s sample came to 55 MB, which the scene cap of 40 MB refused
    assert render.settings_for_length(87).max_output_mb == 87 * render.MAX_MB_PER_SECOND
    assert render.settings_for_length(87).max_output_mb > 55.1
