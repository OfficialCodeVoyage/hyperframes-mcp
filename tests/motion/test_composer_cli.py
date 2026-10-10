"""``python -m hyperframes_mcp.motion compose|build|master``: each command once, offline, with the exit codes the docs promise."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperframes_mcp.hfcli.cli import RenderFailed
from hyperframes_mcp.motion import __main__ as cli
from hyperframes_mcp.motion import composing, hf_cli, manifest, render, video_cli
from hyperframes_mcp.qa.models import QAToolError, Tools
from tests.motion.composer_helpers import edited, sample_shotlist
from tests.motion.master_fakes import FakeFfmpeg, summary

WORDS = [
    {"word": "one", "start": 0.0, "end": 0.4},
    {"word": "ledger", "start": 6.0, "end": 6.5},
    {"word": "done", "start": 13.0, "end": 13.5},
]


@pytest.fixture
def workspace(tmp_path, monkeypatch) -> dict[str, Path]:
    assets = tmp_path / "assets"
    for name in ("audio/voice.wav", "audio/bed.wav", "audio/whoosh.wav"):
        (assets / name).parent.mkdir(parents=True, exist_ok=True)
        (assets / name).write_bytes(b"audio")
    shotlist = tmp_path / "shots.json"
    shotlist.write_text(json.dumps(sample_shotlist()), encoding="utf-8")
    alignment = tmp_path / "words.json"
    alignment.write_text(json.dumps(WORDS), encoding="utf-8")
    # a probe that says the voice is 18 s long, so the test needs no real audio or ffprobe
    monkeypatch.setattr(composing, "resolve_tools", lambda env=None: Tools("f", "p"))
    monkeypatch.setattr(
        composing, "probe_media", lambda path, tools: {"format": {"duration": "18.0"}}
    )
    return {
        "assets": assets,
        "shotlist": shotlist,
        "alignment": alignment,
        "tmp": tmp_path,
    }


def args(ws, command, *extra) -> list[str]:
    return [
        command,
        str(ws["shotlist"]),
        "--alignment",
        str(ws["alignment"]),
        "--assets",
        str(ws["assets"]),
        *extra,
    ]


def test_check_prints_the_resolved_timeline_and_writes_nothing(workspace, capsys):
    out = workspace["tmp"] / "project"

    code = cli.main(args(workspace, "compose", "--check", "--out", str(out)))

    text = capsys.readouterr().out
    assert code == 0
    assert "shot s01  0.000 -> 6.000 s  (cut in)" in text
    assert "shot s02  5.500 -> 12.000 s  (dissolve 0.5 s in)" in text
    assert "  layer 2 lower-third  0.300 -> 6.000 s  slot 5.7 s" in text
    assert "total 18.000 s" in text and "the shot list is valid" in text
    assert not out.exists()


def test_check_on_build_needs_no_out_and_renders_nothing(
    workspace, capsys, monkeypatch
):
    def refuse(*a, **k):
        raise AssertionError("a check must not render")

    monkeypatch.setattr(render, "render_project", refuse)

    assert cli.main(args(workspace, "build", "--check")) == 0
    assert "the shot list is valid" in capsys.readouterr().out


def test_a_shot_list_that_breaks_a_rule_exits_2_and_names_the_shot_layer_and_variable(
    workspace, capsys
):
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1]["variables"].pop("role")
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")

    code = cli.main(args(workspace, "compose", "--check"))

    err = capsys.readouterr().err
    assert code == 2
    assert (
        "shot 's01', layer 2 (lower-third), variable 'role' (string): not given" in err
    )


def test_the_preflight_stops_check_compose_and_build_before_anything_is_written_or_rendered(
    workspace, capsys, monkeypatch
):
    def refuse(*a, **k):
        raise AssertionError("a refused shot list must not render")

    monkeypatch.setattr(render, "render_project", refuse)
    # "The End" is two words: 4.5 s is the static floor, the block itself needs 4.55 s
    raw = edited(
        sample_shotlist(),
        (("shots", 2, "layers", 0, "duration"), 4.5),
    )
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")
    out = workspace["tmp"] / "project"

    for command, extra in (
        ("compose", ("--check",)),
        ("build", ("--check",)),
        ("compose", ("--out", str(out))),
        ("build", ("--out", str(workspace["tmp"] / "v.mp4"))),
    ):
        assert cli.main(args(workspace, command, *extra)) == 2
        assert (
            "shot 's03', layer 1 (act-title): a slot of 4.5 s is shorter than the 4.55 s"
            in capsys.readouterr().err
        )
    assert not out.exists()


def test_a_hidden_layer_is_a_printed_warning_in_check_and_in_compose(workspace, capsys):
    # the lower third sits on track 0, under the act title's night ground: it never shows
    raw = edited(sample_shotlist(), (("shots", 0, "layers", 1, "track"), 0))
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")

    assert cli.main(args(workspace, "compose", "--check")) == 0
    assert (
        "warning: shot 's01', layer 2 (lower-third) is hidden for all of its 5.70 s"
        in capsys.readouterr().out
    )

    assert (
        cli.main(args(workspace, "compose", "--out", str(workspace["tmp"] / "p"))) == 0
    )
    assert (
        "warning: shot 's01', layer 2 (lower-third) is hidden"
        in capsys.readouterr().err
    )


def test_a_shot_list_that_is_not_json_or_has_a_bad_alignment_exits_2(workspace, capsys):
    workspace["shotlist"].write_text("{", encoding="utf-8")
    assert cli.main(args(workspace, "compose", "--check")) == 2
    assert "cannot read the shot list" in capsys.readouterr().err

    workspace["shotlist"].write_text(json.dumps(sample_shotlist()), encoding="utf-8")
    workspace["alignment"].write_text('{"not": "a list"}', encoding="utf-8")
    assert cli.main(args(workspace, "compose", "--check")) == 2
    assert "must be a JSON list" in capsys.readouterr().err


def test_cues_in_the_shot_list_are_resolved_with_the_alignment_given(workspace, capsys):
    raw = edited(
        sample_shotlist(),
        (("shots", 0, "duration"), {"cue": "ledger"}),
        (("shots", 1, "start"), {"cue": "ledger", "offset": -0.5}),
    )
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")

    assert cli.main(args(workspace, "compose", "--check")) == 0
    assert "shot s01  0.000 -> 6.000 s" in capsys.readouterr().out


def test_compose_writes_the_project_and_says_what_it_wrote(workspace, capsys):
    out = workspace["tmp"] / "project"

    code = cli.main(args(workspace, "compose", "--out", str(out)))

    assert code == 0
    assert (out / "index.html").is_file()
    assert (out / "compositions" / "lower-third.html").is_file()
    assert re.search(
        r"wrote \d+ files to .*\(3 shots, 18.000 s\)", capsys.readouterr().out
    )


def test_compose_leaves_notes_for_the_desktop_apps_claude_in_the_project(
    workspace,
):
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out))) == 0

    notes = (out / "VIDEO.md").read_text(encoding="utf-8")
    assert "mcp__hyperframes-edit__" in notes and "Never open `.env`" in notes


def test_compose_records_the_profile_in_the_projects_manifest(workspace):
    out = workspace["tmp"] / "project"

    code = cli.main(args(workspace, "compose", "--out", str(out), "--profile", "docs"))

    saved = manifest.read_manifest(out)
    assert code == 0
    assert (saved.profile, saved.topic_id) == ("docs", None)
    assert saved.shotlist == workspace["shotlist"]


def test_compose_without_a_profile_records_the_default_profile(workspace):
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out))) == 0

    saved = manifest.read_manifest(out)
    assert (saved.profile, saved.topic_id) == ("default", None)


def test_compose_counts_the_notes_and_the_manifest_among_the_files_it_wrote(
    workspace, capsys
):
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out))) == 0

    on_disk = sum(1 for path in out.rglob("*") if path.is_file())
    assert f"wrote {on_disk} files to {out}" in capsys.readouterr().out


def test_build_takes_the_same_profile_flag(workspace):
    assert cli.main(args(workspace, "build", "--check", "--profile", "docs")) == 0


def test_compose_measures_the_sound_effects_so_ones_apart_in_time_share_a_studio_row(
    workspace, monkeypatch
):
    raw = sample_shotlist()
    raw["audio"]["sfx"] = [
        {"path": "audio/whoosh.wav", "at": at, "gain_db": -6.0} for at in (1, 4)
    ]
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")
    lengths = {"voice.wav": "18.0", "whoosh.wav": "1.0"}
    probed: list[str] = []

    def probe(path, tools):
        probed.append(Path(path).name)
        seconds = lengths[Path(path).name]
        return {
            "format": {"duration": seconds},
            "streams": [{"codec_type": "audio", "duration": seconds}],
        }

    monkeypatch.setattr(composing, "probe_media", probe)
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out))) == 0

    page = (out / "index.html").read_text(encoding="utf-8")
    rows = re.findall(r'id="sfx-\d" [^>]*data-track-index="(\d+)"', page)
    assert len(rows) == 2 and rows[0] == rows[1]
    assert probed.count("whoosh.wav") == 1


class FakeHyperframes:
    """Stands in for ``hf_cli.run``: records each call and answers ``open --json`` as given."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.answer = SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr
        )
        self.calls = []

    def __call__(self, project, node_dir, *argv, **kwargs):
        self.calls.append((node_dir, argv))
        return self.answer


def test_compose_open_hands_the_project_to_the_desktop_app(
    workspace, capsys, monkeypatch
):
    fake = FakeHyperframes(0, json.dumps({"opened": True}))
    monkeypatch.setattr(hf_cli, "run", fake)
    out, node = workspace["tmp"] / "project", workspace["tmp"] / "node"

    code = cli.main(
        args(workspace, "compose", "--out", str(out), "--open", "--node-dir", str(node))
    )

    assert code == 0
    assert (out / "index.html").is_file()
    assert fake.calls == [(node, ("open", str(out), "--json"))]
    assert "the HyperFrames desktop app" in capsys.readouterr().out


def test_compose_open_without_the_app_says_where_to_get_it_and_keeps_the_project(
    workspace, capsys, monkeypatch
):
    fake = FakeHyperframes(
        1,
        json.dumps(
            {
                "opened": False,
                "reason": "not-installed",
                "downloadUrl": "https://hyperframes.dev/download",
            }
        ),
    )
    monkeypatch.setattr(hf_cli, "run", fake)
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out), "--open")) == 1

    assert (out / "index.html").is_file()
    assert len(fake.calls) == 1  # no browser fallback
    err = capsys.readouterr().err
    assert "not-installed" in err and "https://hyperframes.dev/download" in err


def test_compose_open_of_a_folder_the_cli_refuses_says_its_error(
    workspace, capsys, monkeypatch
):
    answer = {
        "ok": False,
        "error": {"code": "invalid-project", "message": "no index.html"},
    }
    monkeypatch.setattr(hf_cli, "run", FakeHyperframes(1, json.dumps(answer)))
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out), "--open")) == 1
    assert "did not open it: no index.html" in capsys.readouterr().err


def test_compose_open_whose_cli_fails_without_json_exits_1_with_its_last_line(
    workspace, capsys, monkeypatch
):
    monkeypatch.setattr(hf_cli, "run", FakeHyperframes(1, stderr="boom\nnode crashed"))
    out = workspace["tmp"] / "project"

    assert cli.main(args(workspace, "compose", "--out", str(out), "--open")) == 1
    assert (out / "index.html").is_file()
    assert "did not open it: node crashed" in capsys.readouterr().err


def test_compose_without_open_opens_nothing(workspace, monkeypatch):
    fake = FakeHyperframes(0)
    monkeypatch.setattr(hf_cli, "run", fake)

    out = workspace["tmp"] / "project"
    assert cli.main(args(workspace, "compose", "--out", str(out))) == 0
    assert fake.calls == []


def test_master_sets_the_loudness_of_a_video_exported_by_hand(
    tmp_path, capsys, monkeypatch
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ffmpeg = FakeFfmpeg(bin_dir)
    ffmpeg.measures(summary(-14.0, -1.3, 6.0))
    monkeypatch.setenv("FFMPEG_BIN", str(ffmpeg.path))
    export = tmp_path / "studio-export.mp4"
    export.write_bytes(b"video")
    out = tmp_path / "final.mp4"

    code = cli.main(["master", str(export), "--out", str(out)])

    text = capsys.readouterr().out
    assert code == 0
    assert "mastered" in text and "-14.0 LUFS, -1.3 dBTP" in text
    assert f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}" in text
    assert "loudnorm=I=-14:TP=-1:LRA=11:print_format=json" in ffmpeg.calls()[0]


def test_master_exits_1_when_ffmpeg_fails(tmp_path, capsys, monkeypatch):
    def failing(*a, **k):
        raise video_cli.master.MasterError("the video has no audio stream")

    monkeypatch.setattr(video_cli.master, "master", failing)
    export = tmp_path / "e.mp4"
    export.write_bytes(b"video")

    assert cli.main(["master", str(export), "--out", str(tmp_path / "o.mp4")]) == 1
    assert "no audio stream" in capsys.readouterr().err


def test_compose_and_build_without_out_exit_2(workspace, capsys):
    assert cli.main(args(workspace, "compose")) == 2
    assert cli.main(args(workspace, "build")) == 2
    assert "--out is required" in capsys.readouterr().err


def test_an_unknown_voice_length_is_a_note_not_a_failure(
    workspace, capsys, monkeypatch
):
    def missing(env=None):
        raise QAToolError("ffprobe not found")

    monkeypatch.setattr(composing, "resolve_tools", missing)

    assert cli.main(args(workspace, "compose", "--check")) == 0
    assert (
        "the voice's length is not checked: ffprobe not found"
        in capsys.readouterr().err
    )


def test_build_composes_renders_and_masters_and_prints_the_numbers(
    workspace, capsys, monkeypatch
):
    seen = {}

    def fake_render(project, output, *, node_dir, settings, **kwargs):
        seen["node_dir"], seen["index"] = (
            node_dir,
            (Path(project) / "index.html").is_file(),
        )
        seen["cap_mb"] = settings.max_output_mb
        Path(output).write_bytes(b"raw video")
        return render.RenderResult(
            path=Path(output), duration_s=18.0, width=1920, height=1080, fps=24,
            size_bytes=9, sha256="x", render_seconds=3.0, isolation="sandbox",
        )  # fmt: skip

    monkeypatch.setattr(render, "render_project", fake_render)
    bin_dir = workspace["tmp"] / "bin"
    bin_dir.mkdir()
    ffmpeg = FakeFfmpeg(bin_dir)
    ffmpeg.measures(summary(-14.0, -1.4, 5.5))
    monkeypatch.setenv("FFMPEG_BIN", str(ffmpeg.path))
    out = workspace["tmp"] / "final" / "video.mp4"
    node = workspace["tmp"] / "node"

    code = cli.main(
        args(workspace, "build", "--out", str(out), "--node-dir", str(node))
    )

    text = capsys.readouterr().out
    assert code == 0
    assert seen["node_dir"] == node and seen["index"] is True
    # the size cap fits the length of the video, not a scene clip
    assert seen["cap_mb"] >= render.MAX_MB_PER_SECOND * 18.0
    assert "rendered 1920x1080 24 fps h264, 18.00 s (planned 18.00 s)" in text
    assert "mastered" in text and "-14.0 LUFS, -1.4 dBTP, loudness range 5.5 LU" in text
    assert f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}" in text
    # the master targets of the shot list reach ffmpeg
    assert "loudnorm=I=-14:TP=-1:LRA=11:print_format=json" in ffmpeg.calls()[0]


def test_build_exits_1_when_the_render_or_the_master_fails(
    workspace, capsys, monkeypatch
):
    def failing(*a, **k):
        raise RenderFailed("renderer exited with status 1: a block threw")

    monkeypatch.setattr(render, "render_project", failing)

    code = cli.main(args(workspace, "build", "--out", str(workspace["tmp"] / "v.mp4")))

    assert code == 1
    assert "a block threw" in capsys.readouterr().err
    assert not (workspace["tmp"] / "v.mp4").exists()


def test_the_existing_subcommands_are_untouched(capsys):
    assert cli.main(["list"]) == 0
    assert "credit-line" in capsys.readouterr().out


# -- a video-shot clip: ffprobe tells how long it is --------------------------------------------


def clip_workspace(ws, monkeypatch, *, trim=1, hold="loop", clip_s=5.0):
    """The sample shot list plus a fourth shot with a clip and its credit; ffprobe says the voice is 30 s, the clip clip_s."""
    (ws["assets"] / "clips").mkdir()
    (ws["assets"] / "clips" / "shot.mp4").write_bytes(b"clip")
    raw = sample_shotlist()
    raw["shots"].append(
        {
            "id": "s04",
            "start": 18,
            "duration": 8,
            "layers": [
                {
                    "component": "video-shot",
                    "at": 0,
                    "variables": {
                        "video": "clips/shot.mp4",
                        "trimStart": trim,
                        "grade": "{}",
                        "scrim": "bottom",
                        "hold": hold,
                    },
                },
                {
                    "component": "credit-line",
                    "at": 0.3,
                    "variables": {"text": "AI-generated illustration", "align": "left"},
                },
            ],
        }
    )
    ws["shotlist"].write_text(json.dumps(raw), encoding="utf-8")

    def probe(path, tools):
        if Path(path).name == "shot.mp4":
            return {
                "streams": [{"codec_type": "video", "duration": str(clip_s)}],
                "format": {"duration": str(clip_s + 0.5)},
            }
        return {"format": {"duration": "30.0"}}

    monkeypatch.setattr(composing, "probe_media", probe)


def test_a_slot_longer_than_the_clip_is_a_printed_warning_naming_the_hold(
    workspace, capsys, monkeypatch
):
    clip_workspace(workspace, monkeypatch)

    code = cli.main(args(workspace, "build", "--check"))

    out = capsys.readouterr().out
    assert code == 0
    # the clip's video stream is 5 s (the container is longer: its sound runs on), 4 s after the trim, in an 8 s slot
    assert (
        "warning: shot 's04', layer 1 (video-shot): the slot is 8 s but the clip is only 4 s long after "
        "trimStart 1 s; hold is loop, so it plays again from trimStart, 2 times in all"
        in out
    )
    assert "the shot list is valid" in out


def test_a_trim_start_beyond_the_clip_exits_2_and_writes_nothing(
    workspace, capsys, monkeypatch
):
    clip_workspace(workspace, monkeypatch, trim=6)
    out = workspace["tmp"] / "project"

    for command, extra in (
        ("compose", ("--check",)),
        ("compose", ("--out", str(out))),
        ("build", ("--out", str(workspace["tmp"] / "v.mp4"))),
    ):
        assert cli.main(args(workspace, command, *extra)) == 2
        assert (
            "shot 's04', layer 1 (video-shot), variable 'trimStart': 6 s is not before the end of the clip, "
            "which is 5.00 s long" in capsys.readouterr().err
        )
    assert not out.exists()


def test_a_clip_ffprobe_cannot_read_exits_2_and_names_why(
    workspace, capsys, monkeypatch
):
    clip_workspace(workspace, monkeypatch)
    real = composing.probe_media

    def probe(path, tools):
        if Path(path).name == "shot.mp4":
            raise video_cli.UnreadableMediaError("moov atom not found")
        return real(path, tools)

    monkeypatch.setattr(composing, "probe_media", probe)

    assert cli.main(args(workspace, "build", "--check")) == 2
    assert (
        "variable 'video': the clip cannot be read: moov atom not found"
        in capsys.readouterr().err
    )


def test_a_file_without_a_video_stream_is_not_a_clip(workspace, capsys, monkeypatch):
    clip_workspace(workspace, monkeypatch)
    monkeypatch.setattr(
        composing,
        "probe_media",
        lambda path, tools: {
            "streams": [{"codec_type": "audio"}],
            "format": {"duration": "30"},
        },
    )

    assert cli.main(args(workspace, "build", "--check")) == 2
    assert "the clip cannot be read: it has no video stream" in capsys.readouterr().err


def test_without_ffprobe_the_clips_are_not_checked_and_a_note_says_so(
    workspace, capsys, monkeypatch
):
    clip_workspace(workspace, monkeypatch, trim=6)

    def missing(env=None):
        raise QAToolError("ffprobe not found")

    monkeypatch.setattr(composing, "resolve_tools", missing)

    assert cli.main(args(workspace, "build", "--check")) == 0
    err = capsys.readouterr().err
    assert "the clips' lengths are not checked: ffprobe not found" in err
    # a shot list with no clip says nothing about them
    workspace["shotlist"].write_text(json.dumps(sample_shotlist()), encoding="utf-8")
    assert cli.main(args(workspace, "build", "--check")) == 0
    assert "the clips' lengths" not in capsys.readouterr().err
