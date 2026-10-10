"""Edits of a composed project in place: another picture or clip in a layer's place, with a true credit."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from hyperframes_mcp.motion import preflight, project_edit
from hyperframes_mcp.motion.compose import compose
from hyperframes_mcp.motion.project_edit import EditError, layers_from_page, mounts, replace_media
from hyperframes_mcp.motion.shotlist import parse_shotlist
from tests.motion.composer_helpers import (
    clip_layer,
    credit_layer,
    fake_registry,
    fake_shotlist,
    map_layer,
    photo_layer,
)

VOICE = 20.0


def composed(tmp_path: Path, raw: dict) -> tuple[Path, list]:
    """Compose ``raw`` into ``tmp_path/project``; returns the folder and the layers the composer planned."""
    assets = tmp_path / "assets"
    for name in ("photos/mine.jpg", "clips/shot.mp4", "audio/voice.wav"):
        (assets / name).parent.mkdir(parents=True, exist_ok=True)
        (assets / name).write_bytes(name.encode())
    out = tmp_path / "project"
    result = compose(
        parse_shotlist(raw),
        [],
        VOICE,
        out,
        assets_dir=assets,
        registry=fake_registry(tmp_path / "reg"),
    )
    return out, result.layers


def two_shots() -> dict:
    """Shot p1 a photo with its credit, shot m1 a clip with its credit."""
    raw = fake_shotlist()
    raw["shots"][0]["layers"] = [photo_layer(), credit_layer()]
    raw["shots"][1]["layers"] = [
        clip_layer(variables={"trimStart": 1.5}),
        credit_layer(variables={"text": "Pathe newsreel, 1945"}),
    ]
    return raw


@pytest.fixture
def project(tmp_path) -> Path:
    return composed(tmp_path, two_shots())[0]


def mount(project: Path, layer_id: str) -> dict[str, str]:
    """The attributes of one layer's mount, as the page now holds them."""
    page = (project / "index.html").read_text(encoding="utf-8")
    tag = re.search(rf'<div [^>]*id="{layer_id}"[^>]*>', page).group(0)
    return {k: html.unescape(v) for k, v in re.findall(r'([\w-]+)="([^"]*)"', tag)}


def values(project: Path, layer_id: str) -> dict:
    return json.loads(mount(project, layer_id)["data-variable-values"])


def picture(tmp_path: Path, name: str = "vault.png", sidecar: dict | None = None) -> Path:
    path = tmp_path / "new" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a new picture")
    if sidecar is not None:
        path.with_name(path.name + ".json").write_text(json.dumps(sidecar))
    return path


def test_a_photo_takes_the_new_picture_its_label_and_the_credit_the_person_gave(project, tmp_path):
    before = (project / "index.html").read_text(encoding="utf-8")

    done = replace_media(project, "l-p1-1", picture(tmp_path), credit="Bundesarchiv, Bild 183-R")

    shown = values(project, "l-p1-1")
    assert shown["image"] == "assets/media/edits/vault.png"
    assert (project / shown["image"]).read_bytes() == b"a new picture"
    assert mount(project, "l-p1-1")["data-timeline-label"].endswith(": vault")
    assert values(project, "l-p1-2")["text"] == "Bundesarchiv, Bild 183-R"
    assert mount(project, "l-p1-2")["data-timeline-label"] == "p1 · Credit: Bundesarchiv, Bild 183-R"
    assert (done.old, done.new) == ("assets/media/photos/mine.jpg", shown["image"])
    assert done.credit_layer == "l-p1-2"
    # nothing else on the page moved
    after = (project / "index.html").read_text(encoding="utf-8")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(changed) == 2 and len(before.splitlines()) == len(after.splitlines())


def test_a_picture_made_by_the_picture_command_brings_its_ai_credit_and_record(project, tmp_path):
    record = {"ai": True, "credit": project_edit.AI_CREDIT, "prompt": "a vault"}
    source = picture(tmp_path, sidecar=record)

    replace_media(project, "l-p1-1", source)

    assert values(project, "l-p1-2")["text"] == project_edit.AI_CREDIT
    kept = project / "assets/media/edits/vault.png.json"
    assert json.loads(kept.read_text())["prompt"] == "a vault"


def test_a_picture_made_in_the_edits_folder_is_used_where_it_is(project):
    made = project / project_edit.EDITS_DIR / "vault.png"
    made.parent.mkdir(parents=True)
    made.write_bytes(b"made by picture")
    made.with_name("vault.png.json").write_text(json.dumps({"credit": project_edit.AI_CREDIT}))

    replace_media(project, "l-p1-1", made)

    assert values(project, "l-p1-1")["image"] == "assets/media/edits/vault.png"
    assert sorted(p.name for p in made.parent.iterdir()) == [
        "vault.png",
        "vault.png.json",
    ]


def test_an_outside_picture_without_a_credit_is_refused_and_nothing_changes(project, tmp_path):
    before = (project / "index.html").read_text(encoding="utf-8")

    with pytest.raises(EditError, match=r"whose picture vault.png is \(credit\)"):
        replace_media(project, "l-p1-1", picture(tmp_path))

    assert (project / "index.html").read_text(encoding="utf-8") == before
    assert not (project / "assets/media/edits").exists()


def test_a_clip_takes_the_new_file_from_its_start_and_a_short_one_is_a_warning(project, tmp_path):
    clip = tmp_path / "new" / "pour.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"a new clip")

    done = replace_media(
        project,
        "l-m1-1",
        clip,
        credit="AI-generated clip",
        clip_seconds=lambda path: 2.0,
    )

    shown = values(project, "l-m1-1")
    assert shown["video"] == "assets/media/edits/pour.mp4"
    assert "trimStart" not in shown  # the old trim belonged to the old clip
    assert values(project, "l-m1-2")["text"] == "AI-generated clip"
    assert any("2.00 s" in w and "last frame" in w for w in done.warnings)


def test_the_file_must_suit_the_layer(project, tmp_path):
    clip = tmp_path / "pour.mp4"
    clip.write_bytes(b"a clip")

    with pytest.raises(EditError, match="picture"):
        replace_media(project, "l-p1-1", clip, credit="x")
    with pytest.raises(EditError, match="clip"):
        replace_media(project, "l-m1-1", picture(tmp_path), credit="x")


def test_only_a_photo_or_a_clip_layer_can_be_replaced_and_an_unknown_id_is_named(project, tmp_path):
    with pytest.raises(EditError, match="credit-line"):
        replace_media(project, "l-p1-2", picture(tmp_path), credit="x")
    with pytest.raises(EditError, match="l-x9-1"):
        replace_media(project, "l-x9-1", picture(tmp_path), credit="x")


def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(project, tmp_path):
    first = picture(tmp_path)
    replace_media(project, "l-p1-1", first, credit="a")
    second = tmp_path / "other" / "vault.png"
    second.parent.mkdir()
    second.write_bytes(b"another picture")

    replace_media(project, "l-p1-1", second, credit="b")

    assert values(project, "l-p1-1")["image"] == "assets/media/edits/vault-2.png"
    assert (project / "assets/media/edits/vault.png").read_bytes() == b"a new picture"


def test_two_credit_lines_on_screen_make_the_person_name_one_by_credit_layer(
    tmp_path,
):
    raw = two_shots()
    raw["shots"][0]["layers"].append(credit_layer(at=1.0, variables={"text": "A second line"}))
    project, _ = composed(tmp_path, raw)

    with pytest.raises(EditError, match=r"\(credit_layer\)"):
        replace_media(project, "l-p1-1", picture(tmp_path), credit="x")
    with pytest.raises(EditError, match="l-p1-2, l-p1-3"):
        replace_media(project, "l-p1-1", picture(tmp_path), credit="x", credit_layer="l-m1-2")
    done = replace_media(project, "l-p1-1", picture(tmp_path), credit="x", credit_layer="l-p1-3")

    assert done.credit_layer == "l-p1-3"
    assert values(project, "l-p1-3")["text"] == "x"
    assert values(project, "l-p1-2")["text"] != "x"


# -- reading a page the app has edited ---------------------------------------------------------------


def stack_order(layers) -> list[str]:
    """The ids of ``layers`` from the back of the page's stack to the front, by ``LayerTiming.depth``."""
    return [f"l-{layer.shot}-{layer.index}" for layer in sorted(layers, key=lambda l: l.depth)]


def test_the_layers_of_a_page_stack_as_the_composer_stacked_them(tmp_path):
    raw = two_shots()
    # a layer last in its shot's list but on track 0 is at the back: the depth order is not the list order
    raw["shots"][1]["layers"].append(map_layer(at=0.1, track=0))
    project, planned = composed(tmp_path, raw)
    page = (project / "index.html").read_text(encoding="utf-8")

    layers = layers_from_page(page)

    assert [f"l-{l.shot}-{l.index}" for l in layers] == [f"l-{l.shot}-{l.index}" for l in planned]
    shots = {"p1": 0, "m1": 1}
    expected = sorted(planned, key=lambda l: (shots[l.shot], l.depth))
    assert stack_order(layers) == [f"l-{l.shot}-{l.index}" for l in expected]
    assert stack_order(layers)[2:] == ["l-m1-3", "l-m1-1", "l-m1-2"]
    # what the preflight needs: the times and the values as the page holds them
    photo = next(l for l in layers if l.component == "photo-shot")
    assert (photo.start, photo.end, photo.values["image"]) == (
        0.0,
        8.0,
        "assets/media/photos/mine.jpg",
    )


def test_a_page_the_app_rewrote_reads_the_same_whatever_the_class(project):
    page = (project / "index.html").read_text(encoding="utf-8")
    renamed = re.sub(
        r'<div id="(l-[^"]+)" class="clip shot-([^"]+)"',
        r'<div data-hf-id="hf-\1" id="\1" class="clip fade-hole-still"',
        page,
    )
    assert renamed != page and "shot-p1" not in renamed

    assert layers_from_page(renamed) == layers_from_page(page)
    assert list(mounts(renamed)) == ["l-p1-1", "l-p1-2", "l-m1-1", "l-m1-2"]


def test_replace_media_works_on_a_page_the_app_renamed(project, tmp_path):
    index = project / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace('class="clip shot-p1"', 'class="clip fade-hole-still"'),
        encoding="utf-8",
    )

    replace_media(project, "l-p1-1", picture(tmp_path), credit="Bundesarchiv")

    assert values(project, "l-p1-2")["text"] == "Bundesarchiv"


def set_z(page: str, layer_id: str, z: int | None) -> str:
    """``page`` with the z-index of one layer set, or taken out."""
    style = f' style="z-index: {z}"' if z is not None else ""
    return re.sub(rf'(<div [^>]*id="{layer_id}"[^>]*?) style="z-index: \d+"', rf"\1{style}", page)


def test_a_layer_the_app_moved_behind_another_is_below_it_in_the_depth_order(project):
    page = (project / "index.html").read_text(encoding="utf-8")
    assert stack_order(layers_from_page(page))[:2] == ["l-p1-1", "l-p1-2"]

    moved = set_z(page, "l-p1-2", 0)

    # the credit line, put behind its photo, is hidden for all the time they share
    layers = layers_from_page(moved)
    assert stack_order(layers)[:2] == ["l-p1-2", "l-p1-1"]
    problems: list[str] = []
    preflight.check_hidden_layers(
        [l for l in layers if l.shot == "p1"],
        fake_registry(project.parent / "reg"),
        problems,
        [],
    )
    assert len(problems) == 1 and "its credit line (layer 2 (credit-line)) is hidden" in problems[0]
    # and the page as composed has nothing to report
    clean: list[str] = []
    preflight.check_hidden_layers(layers_from_page(page), fake_registry(project.parent / "reg"), clean, [])
    assert clean == []


def test_layers_with_the_same_z_index_stack_in_page_order_and_a_missing_one_counts_as_zero(
    project,
):
    page = (project / "index.html").read_text(encoding="utf-8")
    tied = set_z(set_z(page, "l-p1-1", 7), "l-p1-2", 7)
    assert stack_order(layers_from_page(tied))[-2:] == ["l-p1-1", "l-p1-2"]

    bare = set_z(page, "l-m1-1", None)
    order = stack_order(layers_from_page(bare))
    assert order.index("l-m1-1") < order.index("l-p1-1")


def test_a_mount_keeps_its_timeline_label_as_the_page_holds_it(project):
    page = (project / "index.html").read_text(encoding="utf-8")
    renamed = page.replace("p1 · Credit: US Army Signal Corps, NARA 111-SC-204515", "p1 · Who made it")

    assert mounts(renamed)["l-p1-2"].label == "p1 · Who made it"


def test_the_notes_point_the_apps_claude_at_the_tools_the_limits_and_the_keys(
    project,
):
    notes = project_edit.project_notes(project)

    for tool in (
        "project_info",
        "layer_table",
        "quote_picture",
        "confirm_spend",
        "replace_media",
    ):
        assert f"`{tool}`" in notes
    assert "refuses a call above $1, or one that takes this video\n  past $10" in notes
    assert "budget add" not in notes
    assert "Never open `.env`" in notes


def test_the_notes_carry_the_house_rules_of_the_projects_profile_and_teach_the_free_media_tools(project, profiles_root):
    profiles_root.write("maps", "edit.yaml", "house_rules:\n  - Flat colour only.\n")

    maps = project_edit.project_notes(project, profile_name="maps")
    default = project_edit.project_notes(project)
    bare = project_edit.project_notes(project, profile_name="no-bundle")

    assert "## This profile's house rules\n- Flat colour only.\n\n## Without the tools" in maps
    assert "## This profile's house rules" not in default  # no edit.yaml, no rules
    assert "## This profile's house rules" not in bare  # no edit.yaml, no rules
    for notes in (maps, default, bare):
        assert "`search_media`" in notes and "`fetch_media`" in notes
        assert "`style_info`" in notes


def test_the_notes_say_to_restart_the_app_when_the_tools_are_missing_and_never_to_edit_the_page_by_hand(
    project,
):
    notes = " ".join(project_edit.project_notes(project).split())
    without = notes[notes.index("## Without the tools") :]

    assert "If the hyperframes-edit tools are not in your tool list" in without
    assert "say so in your answer" in without
    assert "quit the HyperFrames app completely (Cmd+Q) and reopen the project" in without
    assert "Do not edit `index.html` by hand instead" in without
    assert "the git sha it started from" in notes
