"""The project manifest: written in one step, read back, refused when missing or of another schema, found upward."""

from __future__ import annotations

import pytest

from hyperframes_mcp.motion import manifest
from hyperframes_mcp.motion.manifest import MANIFEST_FILE, ManifestError, ProjectManifest


def test_a_manifest_round_trips_with_absolute_sources_and_the_tokens_canvas(tmp_path):
    shotlist = tmp_path / "src" / "shotlist.json"
    shotlist.parent.mkdir()
    shotlist.write_text("{}")
    written = ProjectManifest(
        profile="history",
        topic_id=7,
        shotlist=shotlist,
        shotlist_sha256=manifest.file_sha256(shotlist),
    )

    path = manifest.write_manifest(tmp_path / "project", written)

    assert path == tmp_path / "project" / MANIFEST_FILE
    read = manifest.read_manifest(tmp_path / "project")
    assert read == written
    assert read.canvas == manifest.default_canvas()
    assert (read.canvas.width, read.canvas.height) == (1920, 1080)


def test_a_project_without_a_manifest_names_the_ways_to_get_one(tmp_path):
    with pytest.raises(ManifestError, match="adopt"):
        manifest.read_manifest(tmp_path)


def test_a_manifest_of_another_schema_or_with_unknown_fields_is_refused(tmp_path):
    (tmp_path / MANIFEST_FILE).write_text('{"schema_version": 2}')
    with pytest.raises(ManifestError, match="schema version 2"):
        manifest.read_manifest(tmp_path)

    (tmp_path / MANIFEST_FILE).write_text('{"colour": "typo"}')
    with pytest.raises(ManifestError, match="not a valid manifest"):
        manifest.read_manifest(tmp_path)


def test_the_project_is_found_from_a_folder_inside_it(tmp_path):
    manifest.write_manifest(tmp_path, ProjectManifest())
    inside = tmp_path / "assets" / "media"
    inside.mkdir(parents=True)

    assert manifest.find_project(inside) == tmp_path.resolve()
    assert manifest.find_project(tmp_path.parent) is None


def test_a_manifest_written_with_a_channel_slug_still_loads_as_the_profile(tmp_path):
    (tmp_path / MANIFEST_FILE).write_text('{"channel_slug": "history"}')

    assert manifest.read_manifest(tmp_path).profile == "history"
