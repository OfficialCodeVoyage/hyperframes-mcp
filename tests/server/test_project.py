"""Finding a call's project (named, the session's folder, or a folder inside it) and keeping writes inside it."""

from __future__ import annotations

import pytest

from hyperframes_mcp.motion.manifest import ManifestError, ProjectManifest, write_manifest
from hyperframes_mcp.server.project import ProjectError, resolve_project, writable


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "gold"
    write_manifest(root, ProjectManifest(profile="history", topic_id=3))
    (root / "assets" / "media").mkdir(parents=True)
    return root


def test_a_named_project_or_a_folder_inside_it_resolves_to_the_project(project):
    for start in (project, project / "assets" / "media"):
        root, manifest = resolve_project(str(start))
        assert root == project.resolve()
        assert (manifest.profile, manifest.topic_id) == ("history", 3)


def test_without_a_name_the_sessions_folder_is_used_then_the_working_folder(project, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project / "assets"))
    assert resolve_project()[0] == project.resolve()

    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.chdir(project)
    assert resolve_project()[0] == project.resolve()


def test_a_folder_without_a_manifest_says_how_to_get_one(tmp_path):
    with pytest.raises(ProjectError, match="adopt"):
        resolve_project(str(tmp_path))


def test_a_broken_manifest_is_reported_not_guessed(tmp_path):
    (tmp_path / "video.project.json").write_text("{not json")
    with pytest.raises(ManifestError):
        resolve_project(str(tmp_path))


def test_writes_stay_inside_the_project(project, tmp_path):
    assert writable(project, "assets/media/edits/a.png") == (project / "assets" / "media" / "edits" / "a.png").resolve()
    for outside in ("../elsewhere.png", str(tmp_path / "x.png")):
        with pytest.raises(ProjectError, match="outside the project"):
            writable(project, outside)
    (project / "link").symlink_to(tmp_path)
    with pytest.raises(ProjectError):
        writable(project, "link/x.png")
