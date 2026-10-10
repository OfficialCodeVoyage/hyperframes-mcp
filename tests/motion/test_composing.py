"""``src/hyperframes_mcp/motion/composing.py``: composing a shot list into a project as a library call, offline.

A probe stands in for ffprobe (the voice is 18 s long), so no test below needs real audio or ffprobe.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hyperframes_mcp.hfcli.cli import cli_script
from hyperframes_mcp.motion import composing, manifest
from hyperframes_mcp.motion.project_edit import NOTES_FILE
from hyperframes_mcp.motion.shotlist import ShotListError
from hyperframes_mcp.qa.models import QAToolError, Tools
from tests.motion.composer_helpers import edited, sample_shotlist

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
    monkeypatch.setattr(composing, "resolve_tools", lambda env=None: Tools("f", "p"))
    monkeypatch.setattr(
        composing,
        "probe_media",
        lambda path, tools: {
            "format": {"duration": "18.0"},
            "streams": [{"codec_type": "audio", "duration": "1.0"}],
        },
    )
    return {
        "assets": assets,
        "shotlist": shotlist,
        "alignment": alignment,
        "out": tmp_path / "project",
    }


def compose_it(ws, **more) -> composing.ComposedProject:
    return composing.compose_project(ws["shotlist"], ws["alignment"], ws["out"], assets=ws["assets"], **more)


def test_the_manifest_records_the_profile_the_topic_and_what_the_project_was_composed_from(workspace, tmp_path):
    node = tmp_path / "node"
    package = cli_script(node).parents[1] / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"version": "0.8.139"}))

    composed = compose_it(workspace, profile_name="gold", topic_id=7, node_dir=node)

    saved = manifest.read_manifest(workspace["out"])
    assert saved == composed.manifest
    assert (saved.profile, saved.topic_id, saved.title) == (
        "gold",
        7,
        "Test video",
    )
    assert saved.shotlist == workspace["shotlist"]
    assert saved.shotlist_sha256 == hashlib.sha256(workspace["shotlist"].read_bytes()).hexdigest()
    assert saved.alignment == workspace["alignment"]
    assert saved.assets == workspace["assets"]
    assert saved.voice == workspace["assets"] / "audio" / "voice.wav"
    assert saved.canvas == manifest.default_canvas()
    assert saved.hyperframes_version == "0.8.139"


def test_the_profile_defaults_to_default_the_topic_to_none_and_the_voice_can_be_named(workspace, tmp_path):
    other_voice = tmp_path / "take3.wav"

    saved = compose_it(workspace, voice=other_voice, node_dir=tmp_path / "no-node").manifest

    assert (saved.profile, saved.topic_id) == ("default", None)
    assert saved.voice == other_voice
    # no HyperFrames installed in that node folder: the version is simply not known
    assert saved.hyperframes_version is None


def test_paths_in_the_manifest_are_absolute_even_when_the_call_gave_relative_ones(workspace, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    composed = composing.compose_project(
        Path("shots.json"),
        Path("words.json"),
        Path("project"),
        assets=Path("assets"),
    )

    saved = composed.manifest
    for path in (saved.shotlist, saved.alignment, saved.assets, saved.voice):
        assert path is not None and path.is_absolute()
    assert saved.shotlist == workspace["shotlist"]
    assert composed.project_dir == workspace["out"]


def test_without_an_alignment_the_manifest_has_none(workspace):
    composed = composing.compose_project(workspace["shotlist"], None, workspace["out"], assets=workspace["assets"])

    assert composed.manifest.alignment is None
    assert manifest.read_manifest(workspace["out"]).alignment is None


def test_it_writes_the_project_the_notes_and_the_manifest_and_lists_them(workspace):
    composed = compose_it(workspace)

    out = workspace["out"]
    assert (out / "index.html").is_file() and (out / NOTES_FILE).is_file()
    assert (out / manifest.MANIFEST_FILE).is_file()
    assert "Never open `.env`" in (out / NOTES_FILE).read_text(encoding="utf-8")
    assert composed.files[-2:] == [NOTES_FILE, manifest.MANIFEST_FILE]
    assert sorted(composed.files) == sorted(
        path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
    )
    assert (len(composed.result.shots), composed.result.total_s) == (3, 18.0)


def test_warnings_and_notes_come_back_as_data_and_nothing_is_printed(workspace, monkeypatch, capsys):
    # the lower third sits on track 0, under the act title's night ground: it never shows
    raw = edited(sample_shotlist(), (("shots", 0, "layers", 1, "track"), 0))
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")

    def missing(env=None):
        raise QAToolError("ffprobe not found")

    monkeypatch.setattr(composing, "resolve_tools", missing)

    composed = compose_it(workspace)

    assert any("shot 's01', layer 2 (lower-third) is hidden" in warning for warning in composed.warnings)
    assert composed.notes == [
        "the voice's length is not checked: ffprobe not found",
        "the sound effects' lengths are not checked: ffprobe not found",
    ]
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_a_shot_list_that_breaks_a_rule_writes_nothing_not_even_the_manifest(workspace):
    raw = sample_shotlist()
    raw["shots"][0]["layers"][1]["variables"].pop("role")
    workspace["shotlist"].write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ShotListError, match="variable 'role'"):
        compose_it(workspace)

    assert not workspace["out"].exists()


def test_load_inputs_measures_the_voice_and_says_what_it_could_not_check(workspace, monkeypatch):
    inputs = composing.load_inputs(workspace["shotlist"], workspace["alignment"], workspace["assets"])

    assert inputs.voice_duration == 18.0
    assert [word.word for word in inputs.alignment] == ["one", "ledger", "done"]
    assert inputs.clip_seconds is None  # no layer names a clip
    assert inputs.notes == []

    def missing(env=None):
        raise QAToolError("ffprobe not found")

    monkeypatch.setattr(composing, "resolve_tools", missing)
    unchecked = composing.load_inputs(workspace["shotlist"], None, workspace["assets"])
    assert unchecked.voice_duration is None and unchecked.alignment == []
    assert unchecked.notes == ["the voice's length is not checked: ffprobe not found"]
