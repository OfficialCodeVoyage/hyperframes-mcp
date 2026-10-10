"""The motion registry: a closed item schema, and an installer that copies into a HyperFrames project."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from hyperframes_mcp.motion import registry, tokens
from hyperframes_mcp.motion.registry import RegistryError

FONT_LABEL_FILES = {
    "IBMPlexSansCondensed-Medium.ttf",
    "IBMPlexSansCondensed-SemiBold.ttf",
    "IBMPlexSansCondensed-Bold.ttf",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def item_json(name: str = "demo-block", **changes) -> dict:
    item = {
        "name": name,
        "type": "hyperframes:block",
        "title": "Demo block",
        "description": "A block for the tests.",
        "license": "Apache-2.0",
        "files": [
            {
                "path": f"blocks/{name}.html",
                "target": f"compositions/{name}.html",
                "type": "hyperframes:composition",
            }
        ],
        "registryDependencies": [],
        "dimensions": {"width": 1920, "height": 1080},
        "duration": 5,
        "variables": [{"id": "text", "type": "string", "label": "Text", "default": "hello"}],
        "source": None,
        "notes": "",
    }
    item.update(changes)
    return item


def make_registry(tmp_path: Path, *items: dict, html: str = "<p>demo</p>") -> tuple[Path, Path]:
    """A registry.json with ``items`` and the item files it names, under ``tmp_path/lib``."""
    root = tmp_path / "lib"
    root.mkdir(parents=True, exist_ok=True)
    for item in items:
        for entry in item["files"]:
            target = root / entry["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
    path = root / "registry.json"
    path.write_text(json.dumps({"name": "test", "items": list(items)}), encoding="utf-8")
    return path, root


def load(tmp_path: Path, *items: dict, **kwargs) -> registry.Registry:
    path, root = make_registry(tmp_path, *items, **kwargs)
    return registry.load_registry(path, root)


# -- the shipped registry --------------------------------------------------------------------------


def test_the_shipped_registry_lists_the_reference_component():
    names = [item.name for item in registry.list_items()]
    assert "credit-line" in names
    item = registry.get("credit-line")
    assert item.type == "hyperframes:block"
    assert item.dimensions == {"width": 1920, "height": 1080}
    assert [v["id"] for v in item.variables] == ["durationSeconds", "text", "align"]
    assert item.source is None


def test_an_unknown_item_names_what_there_is():
    with pytest.raises(RegistryError, match="credit-line"):
        registry.get("no-such-item")


def test_every_shipped_item_names_files_that_exist_and_a_licence():
    for item in registry.list_items():
        assert item.license.strip(), item.name
        for entry in item.files:
            assert (registry.MOTION_DIR / entry.path).is_file(), entry.path


def test_the_variables_in_the_registry_are_the_ones_the_component_declares():
    for item in registry.list_items():
        for entry in item.files:
            if not entry.path.endswith(".html"):
                continue
            html = (registry.MOTION_DIR / entry.path).read_text(encoding="utf-8")
            declared = re.search(r"data-composition-variables='(\[.*?\])'", html, re.DOTALL)
            assert declared, f"{entry.path} declares no variables"
            assert json.loads(declared.group(1)) == list(item.variables), item.name


def test_a_blocks_size_and_duration_are_the_ones_its_root_declares():
    canvas = tokens.default_tokens().canvas
    for item in registry.list_items():
        if item.type != "hyperframes:block":
            continue
        assert item.dimensions == {"width": canvas["width"], "height": canvas["height"]}
        for entry in item.files:
            html = (registry.MOTION_DIR / entry.path).read_text(encoding="utf-8")
            assert f'data-duration="{item.duration:g}"' in html, item.name
            assert f'data-width="{canvas["width"]}"' in html, item.name
            assert f'data-height="{canvas["height"]}"' in html, item.name


# -- schema ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, match",
    [
        ({"name": "Demo_Block"}, "name"),
        ({"type": "hyperframes:example"}, "type"),
        ({"title": ""}, "title"),
        ({"license": ""}, "license"),
        ({"license": "do what you want"}, "license"),
        ({"files": []}, "files"),
        (
            {
                "files": [
                    {
                        "path": "blocks/demo-block.html",
                        "target": "x.html",
                        "type": "other",
                    }
                ]
            },
            "files",
        ),
        ({"dimensions": None}, "dimensions"),
        ({"duration": 0}, "duration"),
        ({"duration": None}, "duration"),
        ({"registryDependencies": ["no-such-item"]}, "no-such-item"),
        ({"variables": [{"id": "t", "type": "string", "label": "T"}]}, "default"),
        (
            {"variables": [{"id": "t", "type": "weird", "label": "T", "default": "x"}]},
            "type",
        ),
        (
            {"variables": [{"id": "a", "type": "enum", "label": "A", "default": "x"}]},
            "options",
        ),
        (
            {"variables": [{"id": "n", "type": "number", "label": "N", "default": "5"}]},
            "default",
        ),
        ({"source": {"url": "http://x.test/a", "sha": "abc"}}, "source"),
        ({"source": {"url": "https://x.test/a", "sha": "a" * 40}}, "notes"),
        ({"extra": 1}, "extra"),
    ],
)
def test_a_bad_item_is_refused(tmp_path, change, match):
    with pytest.raises(RegistryError, match=match):
        load(tmp_path, item_json(**change))


def test_a_missing_field_is_refused(tmp_path):
    item = item_json()
    del item["notes"]
    with pytest.raises(RegistryError, match="notes"):
        load(tmp_path, item)


def test_a_component_has_no_dimensions_or_duration(tmp_path):
    component = item_json(
        "demo-part",
        type="hyperframes:component",
        files=[
            {
                "path": "components/demo-part.html",
                "target": "compositions/components/demo-part.html",
                "type": "hyperframes:snippet",
            }
        ],
    )
    with pytest.raises(RegistryError, match="dimensions"):
        load(tmp_path, component)
    del component["dimensions"], component["duration"]
    assert load(tmp_path, component).get("demo-part").dimensions is None


def test_a_vendored_item_records_url_sha_and_what_was_changed(tmp_path):
    vendored = item_json(
        source={
            "url": "https://github.com/heygen-com/hyperframes/tree/abc",
            "sha": "b" * 40,
        },
        notes="Changed: variables, tokens, local GSAP.",
    )
    item = load(tmp_path, vendored).get("demo-block")
    assert item.source.sha == "b" * 40
    assert item.source.url.startswith("https://")


def test_an_unknown_top_level_key_or_a_duplicate_name_is_refused(tmp_path):
    path, root = make_registry(tmp_path, item_json())
    raw = json.loads(path.read_text())
    path.write_text(json.dumps({**raw, "homepage": "x"}))
    with pytest.raises(RegistryError, match="homepage"):
        registry.load_registry(path, root)
    path.write_text(json.dumps({"name": "t", "items": [item_json(), item_json()]}))
    with pytest.raises(RegistryError, match="twice"):
        registry.load_registry(path, root)


def test_two_items_may_not_install_to_the_same_path(tmp_path):
    a = item_json("a-block")
    b = item_json("b-block")
    b["files"][0]["target"] = a["files"][0]["target"]
    with pytest.raises(RegistryError, match="same target"):
        load(tmp_path, a, b)


def test_dependencies_may_not_be_circular(tmp_path):
    a = item_json("a-block", registryDependencies=["b-block"])
    b = item_json("b-block", registryDependencies=["a-block"])
    with pytest.raises(RegistryError, match="circular"):
        load(tmp_path, a, b)


@pytest.mark.parametrize(
    "target",
    [
        "/etc/passwd",
        "../outside.html",
        "compositions/../../outside.html",
        "..",
        "a//b.html",
        "",
        "C:/x.html",
        "back\\slash.html",
    ],
)
def test_an_unsafe_target_is_refused_when_loading(tmp_path, target):
    item = item_json()
    item["files"][0]["target"] = target
    with pytest.raises(RegistryError, match="target"):
        load(tmp_path, item)


def test_a_file_path_may_not_leave_the_library(tmp_path):
    item = item_json()
    item["files"][0]["path"] = "../secret.html"
    with pytest.raises(RegistryError, match="path"):
        load(tmp_path, item)


def test_a_file_that_does_not_exist_is_refused(tmp_path):
    path, root = make_registry(tmp_path, item_json())
    (root / "blocks" / "demo-block.html").unlink()
    with pytest.raises(RegistryError, match="does not exist"):
        registry.load_registry(path, root)


# -- install ---------------------------------------------------------------------------------------


def test_install_copies_the_item_and_what_it_needs(tmp_path):
    project = tmp_path / "project"

    written = registry.install("credit-line", project)

    rel = {p.relative_to(project).as_posix() for p in written}
    assert rel == {
        "compositions/credit-line.html",
        "vendor/gsap.min.js",
        "tokens.css",
        *(f"assets/fonts/{name}" for name in FONT_LABEL_FILES),
        "assets/fonts/OFL-IBMPlexSansCondensed.txt",
    }
    assert all(p.is_absolute() for p in written)
    assert (project / "tokens.css").read_text(encoding="utf-8") == tokens.stylesheet()
    assert sha(project / "vendor" / "gsap.min.js") == sha(registry.gsap_source())
    assert not (project / "assets" / "fonts" / "Newsreader-Variable.ttf").exists()  # the component does not use it


def test_install_records_a_hash_of_every_file_in_the_lock(tmp_path):
    project = tmp_path / "project"
    written = registry.install("credit-line", project)

    lock = json.loads((project / registry.LOCK_NAME).read_text(encoding="utf-8"))

    assert lock["version"] == 1
    assert set(lock["files"]) == {p.relative_to(project).as_posix() for p in written}
    for target, entry in lock["files"].items():
        assert entry["sha256"] == sha(project / target)
    assert lock["files"]["compositions/credit-line.html"]["item"] == "credit-line"


def test_installing_twice_writes_nothing_the_second_time(tmp_path):
    project = tmp_path / "project"
    registry.install("credit-line", project)

    assert registry.install("credit-line", project) == []


def test_a_file_the_user_changed_is_kept_without_force(tmp_path):
    project = tmp_path / "project"
    registry.install("credit-line", project)
    mine = project / "compositions" / "credit-line.html"
    mine.write_text("<!-- my edit -->", encoding="utf-8")

    report = registry.install_item("credit-line", project)

    assert report.written == []
    assert report.kept == [mine]
    assert mine.read_text(encoding="utf-8") == "<!-- my edit -->"


def test_force_overwrites_a_changed_file_and_updates_the_lock(tmp_path):
    project = tmp_path / "project"
    registry.install("credit-line", project)
    mine = project / "compositions" / "credit-line.html"
    mine.write_text("<!-- my edit -->", encoding="utf-8")

    written = registry.install("credit-line", project, force=True)

    assert written == [mine]
    assert mine.read_bytes() == (registry.MOTION_DIR / "blocks" / "credit-line.html").read_bytes()
    lock = json.loads((project / registry.LOCK_NAME).read_text(encoding="utf-8"))
    assert lock["files"]["compositions/credit-line.html"]["sha256"] == sha(mine)


def test_a_file_that_was_not_installed_by_us_is_kept(tmp_path):
    project = tmp_path / "project"
    (project / "compositions").mkdir(parents=True)
    theirs = project / "compositions" / "credit-line.html"
    theirs.write_text("<!-- theirs -->", encoding="utf-8")

    report = registry.install_item("credit-line", project)

    assert report.kept == [theirs]
    assert theirs.read_text(encoding="utf-8") == "<!-- theirs -->"
    assert (project / "tokens.css").exists()  # the rest is still installed


def test_an_unchanged_file_is_updated_when_the_library_changes(tmp_path):
    reg = load(tmp_path, item_json(), html="<p>v1</p>")
    project = tmp_path / "project"
    registry.install("demo-block", project, registry=reg)
    (tmp_path / "lib" / "blocks" / "demo-block.html").write_text("<p>v2</p>", encoding="utf-8")

    written = registry.install("demo-block", project, registry=reg)

    assert [p.name for p in written] == ["demo-block.html"]
    assert (project / "compositions" / "demo-block.html").read_text(encoding="utf-8") == "<p>v2</p>"


def test_the_apps_id_stamps_are_not_a_change_of_the_file(tmp_path):
    project = tmp_path / "project"
    registry.install("credit-line", project)
    stamped = project / "compositions" / "credit-line.html"
    original = stamped.read_text(encoding="utf-8")
    stamped.write_text(original.replace("<p ", '<p data-hf-id="hf-abcd" ', 1), encoding="utf-8")
    assert stamped.read_text(encoding="utf-8") != original

    report = registry.install_item("credit-line", project)

    assert report.kept == [] and report.written == [] and stamped in report.unchanged
    assert 'data-hf-id="hf-abcd"' in stamped.read_text(encoding="utf-8")  # left as the app made it


def test_a_page_the_app_re_saved_with_the_same_markup_is_not_a_change_but_other_words_are(
    tmp_path,
):
    # seen live: the app moved the doctype, re-quoted the attributes and re-spaced the text of an installed block
    reg = load(
        tmp_path,
        item_json(),
        html="<!-- note --><!doctype html><p class='a' id=\"x\">v1  text</p>",
    )
    project = tmp_path / "project"
    registry.install("demo-block", project, registry=reg)
    target = project / "compositions" / "demo-block.html"
    target.write_text(
        '<!DOCTYPE html>\n<p id="x"  class="a" data-hf-id="hf-1">\n  v1 text\n</p>\n',
        encoding="utf-8",
    )

    resaved = registry.install_item("demo-block", project, registry=reg)

    assert resaved.kept == [] and resaved.written == [] and target in resaved.unchanged
    target.write_text('<!DOCTYPE html>\n<p id="x" class="a">my words</p>\n', encoding="utf-8")

    edited = registry.install_item("demo-block", project, registry=reg)

    assert edited.kept == [target] and edited.written == []


def test_a_stamped_file_is_still_updated_when_the_library_changes_and_a_real_edit_is_still_kept(
    tmp_path,
):
    reg = load(tmp_path, item_json(), html="<p>v1</p>")
    project = tmp_path / "project"
    registry.install("demo-block", project, registry=reg)
    target = project / "compositions" / "demo-block.html"
    target.write_text('<p data-hf-id="hf-1234">v1</p>', encoding="utf-8")
    (tmp_path / "lib" / "blocks" / "demo-block.html").write_text("<p>v2</p>", encoding="utf-8")

    updated = registry.install_item("demo-block", project, registry=reg)

    assert updated.written == [target] and target.read_text(encoding="utf-8") == "<p>v2</p>"
    target.write_text('<p data-hf-id="hf-1234">my words</p>', encoding="utf-8")
    (tmp_path / "lib" / "blocks" / "demo-block.html").write_text("<p>v3</p>", encoding="utf-8")

    kept = registry.install_item("demo-block", project, registry=reg)

    assert kept.kept == [target] and kept.written == []


def test_dependencies_are_installed_with_the_item(tmp_path):
    reg = load(
        tmp_path,
        item_json("top-block", registryDependencies=["base-block"]),
        item_json("base-block"),
    )
    project = tmp_path / "project"

    written = registry.install("top-block", project, registry=reg)

    names = [p.name for p in written]
    assert names.index("base-block.html") < names.index("top-block.html")


def test_the_fonts_installed_follow_the_variables_the_component_reads(tmp_path):
    html = "<style>.a { font-family: var(--font-mono); } .b { font-family: var(--font-display); }</style>"
    reg = load(tmp_path, item_json(), html=html)
    project = tmp_path / "project"

    written = registry.install("demo-block", project, registry=reg)

    fonts = {p.name for p in written if p.parent.name == "fonts"}
    assert {
        "CourierPrime-Regular.ttf",
        "CourierPrime-Bold.ttf",
        "Newsreader-Variable.ttf",
        "Newsreader-Italic-Variable.ttf",
    } <= fonts
    assert not fonts & FONT_LABEL_FILES


def test_install_refuses_a_project_path_that_escapes_through_a_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "compositions").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RegistryError, match="outside"):
        registry.install("credit-line", project)

    assert list(outside.iterdir()) == []


def test_install_refuses_an_unsafe_target_even_when_the_item_was_built_by_hand(
    tmp_path,
):
    item = registry.Item(
        name="evil-block",
        type="hyperframes:block",
        title="Evil",
        description="x",
        license="MIT",
        files=(registry.RegistryFile("blocks/evil.html", "../escape.html", "hyperframes:composition"),),
        registry_dependencies=(),
        dimensions={"width": 1920, "height": 1080},
        duration=1.0,
        variables=(),
        source=None,
        notes="",
    )
    (tmp_path / "lib" / "blocks").mkdir(parents=True)
    (tmp_path / "lib" / "blocks" / "evil.html").write_text("x", encoding="utf-8")
    reg = registry.Registry(name="t", items={"evil-block": item}, root=tmp_path / "lib")

    with pytest.raises(RegistryError, match="target"):
        registry.install("evil-block", tmp_path / "project", registry=reg)

    assert not (tmp_path / "escape.html").exists()


def test_a_broken_lock_file_is_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / registry.LOCK_NAME).write_text("not json", encoding="utf-8")

    with pytest.raises(RegistryError, match=registry.LOCK_NAME):
        registry.install("credit-line", project)


def test_install_makes_no_network_call(monkeypatch, tmp_path):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the installer must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    registry.install("credit-line", tmp_path / "project")


def test_the_registry_file_is_valid_json_with_a_closed_top_level():
    raw = json.loads(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"name", "items"}
    assert copy.deepcopy(raw) == raw
