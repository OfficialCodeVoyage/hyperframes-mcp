"""Putting one vector graphic in a composed project's page as a ``graphic-stamp`` layer, with the care of ``replace_media``:
the same page write, an id the layer table reads, a z-order above the pictures, and nothing else on the page touched."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from hyperframes_mcp.motion import compose, page_checks, tokens, vectors
from hyperframes_mcp.motion.compose import LayerTiming
from hyperframes_mcp.motion.layer_table import layer_table
from hyperframes_mcp.motion.project_edit import (
    EditError,
    Mount,
    _corner_warnings,
    add_graphic,
    layers_from_page,
    mounts,
)
from tests.motion.test_project_edit import composed, mount, two_shots, values

ARROW = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor">'
    '<path d="M3 12h18M15 6l6 6-6 6"/></svg>'
)


@pytest.fixture
def project(tmp_path) -> Path:
    """p1 (0 to 8 s: a photo and its credit) and m1 (8 to 16 s: a clip and its credit) in a 20 s video, with the vector
    ``arrow`` saved."""
    root = composed(tmp_path, two_shots())[0]
    vectors.save_vector(root, ARROW, "arrow")
    return root


def page(project: Path) -> str:
    return (project / "index.html").read_text(encoding="utf-8")


def z_index(project: Path, layer: str) -> int:
    return int(re.search(r"z-index: (\d+)", mount(project, layer)["style"]).group(1))


def test_one_layer_is_added_after_the_others_and_nothing_else_on_the_page_changes(
    project,
):
    before = page(project)

    added = add_graphic(project, "arrow", 2.0, 4.0)

    after = page(project)
    new = [line for line in after.splitlines() if line not in before.splitlines()]
    assert len(new) == 1 and 'id="l-p1-3"' in new[0]
    assert [line for line in before.splitlines() if line not in after.splitlines()] == []
    assert len(after.splitlines()) == len(before.splitlines()) + 1
    assert (added.layer, added.shot, added.start, added.end) == (
        "l-p1-3",
        "p1",
        2.0,
        6.0,
    )
    assert added.vector == "assets/media/vectors/arrow.svg"
    # it sits among the mounts, before the audio, indented as they are
    lines = after.splitlines()
    index = lines.index(new[0])
    assert 'id="l-m1-2"' in lines[index - 1] and "<audio" in lines[index + 1]
    assert new[0].startswith("      <div id=")


def test_the_mount_is_the_one_the_composer_writes_with_the_variables_of_the_block(
    project,
):
    added = add_graphic(
        project,
        "arrow",
        2.0,
        4.0,
        position="top-right",
        size="large",
        animation="draw-on",
        color="oxblood",
    )

    attrs = mount(project, "l-p1-3")
    assert attrs["class"] == "clip shot-p1"
    assert attrs["data-composition-id"] == "graphic-stamp"
    assert attrs["data-composition-src"] == "compositions/graphic-stamp.html"
    assert (attrs["data-start"], attrs["data-duration"]) == ("2.0", "4.0")
    assert (attrs["data-width"], attrs["data-height"]) == ("1920", "1080")
    assert values(project, "l-p1-3") == {
        "durationSeconds": 4.0,
        "svg": "assets/media/vectors/arrow.svg",
        "position": "top-right",
        "size": "large",
        "color": "oxblood",
        "animation": "draw-on",
    }
    assert attrs["data-timeline-label"] == "p1 · Graphic: arrow"
    # the same text compose would write for that layer
    layer = compose.LayerTiming("p1", 3, "graphic-stamp", 2.0, 6.0, 0, values(project, "l-p1-3"))
    assert page(project).count(compose.mount_html(layer, added.row, added.z_index)) == 1


def test_the_default_options_are_a_stamp_medium_in_the_top_left_corner_in_the_blocks_own_colour(
    project,
):
    add_graphic(project, "arrow", 1.0, 3.0)

    assert values(project, "l-p1-3") == {
        "durationSeconds": 3.0,
        "svg": "assets/media/vectors/arrow.svg",
        "position": "top-left",
        "size": "medium",
        "animation": "stamp",
    }


def test_the_block_comes_with_it_once_and_a_second_graphic_leaves_it_as_it_is(project):
    assert not (project / "compositions/graphic-stamp.html").exists()

    add_graphic(project, "arrow", 1.0, 3.0)
    installed = (project / "compositions/graphic-stamp.html").read_text(encoding="utf-8")
    add_graphic(project, "arrow", 5.0, 3.0)

    assert installed == (tokens.MOTION_DIR / "blocks/graphic-stamp.html").read_text(encoding="utf-8")
    assert (project / "tokens.css").is_file() and (project / "vendor/gsap.min.js").is_file()
    lock = json.loads((project / "motion.lock.json").read_text(encoding="utf-8"))
    assert "compositions/graphic-stamp.html" in str(lock)


def test_the_new_id_follows_the_shot_that_is_on_screen_and_counts_on(project):
    first = add_graphic(project, "arrow", 2.0, 3.0)
    second = add_graphic(project, "arrow", 3.0, 3.0)
    third = add_graphic(project, "arrow", 9.0, 3.0)
    later = add_graphic(project, "arrow", 19.0, 2.4)

    assert [first.layer, second.layer, third.layer] == ["l-p1-3", "l-p1-4", "l-m1-3"]
    assert (third.shot, later.shot) == ("m1", "m1")  # the last shot to have started


def test_it_is_in_front_of_every_layer_on_screen_while_it_shows_the_credit_included(
    project,
):
    # 6 s to 11 s spans the end of p1 and the start of m1
    added = add_graphic(project, "arrow", 6.0, 5.0)

    ceiling = max(z_index(project, layer) for layer in ("l-p1-1", "l-p1-2", "l-m1-1", "l-m1-2"))
    assert added.z_index == ceiling + 1 == z_index(project, "l-p1-3")
    found = mounts(page(project))
    assert found["l-p1-3"].layer.depth > max(found[i].layer.depth for i in ("l-p1-1", "l-m1-1"))


def test_a_graphic_is_only_in_front_of_what_is_on_screen_while_it_shows(
    project,
):
    added = add_graphic(project, "arrow", 1.0, 3.0)  # p1 only

    assert added.z_index == max(z_index(project, "l-p1-1"), z_index(project, "l-p1-2")) + 1


def test_a_graphic_takes_a_studio_row_of_its_own_and_a_later_one_that_does_not_overlap_shares_it(
    project,
):
    rows = [int(mount(project, i)["data-track-index"]) for i in ("l-p1-1", "l-p1-2", "l-m1-1", "l-m1-2")]

    first = add_graphic(project, "arrow", 1.0, 3.0)
    apart = add_graphic(project, "arrow", 6.0, 3.0)
    together = add_graphic(project, "arrow", 2.0, 3.0)

    assert first.row == max(rows) + 1
    assert apart.row == first.row  # free again by then
    assert together.row == first.row + 1  # overlaps the first
    assert int(mount(project, "l-p1-3")["data-track-index"]) == first.row


def test_the_layer_is_read_by_the_layer_table_and_the_checks_find_nothing_wrong_with_it(
    project,
):
    add_graphic(project, "arrow", 2.0, 4.0, position="top-right")

    rows = {r.id: r for r in layer_table(project, kinds=["graphic-stamp"])}
    row = rows["l-p1-3"]
    assert (row.kind, row.source, row.start, row.end) == (
        "graphic-stamp",
        "graphic",
        2.0,
        6.0,
    )
    assert row.text == "assets/media/vectors/arrow.svg"
    layers = {f"l-{layer.shot}-{layer.index}": layer for layer in layers_from_page(page(project))}
    assert layers["l-p1-3"].component == "graphic-stamp"
    assert page_checks.page_findings(page(project)) == []


def test_a_page_the_app_has_edited_still_takes_the_layer(project):
    text = page(project)
    text = re.sub(r'(<div id="l-[^"]+")', r'\1 data-hf-id="x1"', text)
    text = text.replace("></div>", ">\n      </div>")
    (project / "index.html").write_text(text, encoding="utf-8")

    add_graphic(project, "arrow", 2.0, 4.0)

    assert "l-p1-3" in mounts(page(project))
    assert page(project).count("</div>") == text.count("</div>") + 1


def test_a_vector_is_found_by_name_or_by_path_and_must_be_an_svg_inside_the_project(project, tmp_path):
    by_path = add_graphic(project, "assets/media/vectors/arrow.svg", 1.0, 3.0)
    (tmp_path / "outside.svg").write_text(ARROW)
    (project / "notes.txt").write_text("x")
    (project / "x.svg").symlink_to(tmp_path / "outside.svg")

    assert by_path.vector == "assets/media/vectors/arrow.svg"
    for bad, words in {
        "nothing": "no vector",
        "../outside.svg": "outside the project",
        "x.svg": "outside the project",
        "notes.txt": "must be an .svg",
    }.items():
        with pytest.raises(EditError, match=words):
            add_graphic(project, bad, 1.0, 3.0)


def test_a_vector_that_was_edited_into_something_unsafe_is_refused_again_here(project):
    path = project / "assets/media/vectors/arrow.svg"
    path.write_text(path.read_text().replace("<path", "<script>x()</script><path"))

    with pytest.raises(vectors.VectorError, match="script"):
        add_graphic(project, "arrow", 1.0, 3.0)


@pytest.mark.parametrize(
    ("arguments", "words"),
    [
        ({"position": "middle"}, "position 'middle'.*top-left"),
        ({"size": "huge"}, "size 'huge'.*small"),
        ({"animation": "spin"}, "animation 'spin'.*stamp, draw-on, fade"),
        ({"color": "red"}, "color 'red'.*gold"),
        ({"start": -1}, "before the start"),
        ({"start": 20}, "past the end"),
        ({"duration": 2.3}, "at least 2.4 s"),
        ({"duration": 31}, "at most 30 s"),
    ],
)
def test_a_wrong_option_or_time_is_refused_in_words_and_the_page_is_untouched(project, arguments, words):
    before = page(project)
    call = {"start": 2.0, "duration": 4.0} | arguments

    with pytest.raises(EditError, match=words):
        add_graphic(project, "arrow", **call)

    assert page(project) == before
    assert not (project / "compositions/graphic-stamp.html").exists()


def test_a_graphic_that_runs_past_the_end_of_the_video_is_a_warning(project):
    added = add_graphic(project, "arrow", 18.0, 4.0)

    assert any("ends at 22" in w and "20" in w for w in added.warnings)


def test_a_vector_with_a_credit_in_its_record_says_it_needs_one_on_screen(project):
    record = project / "assets/media/vectors/arrow.svg.json"
    data = json.loads(record.read_text())
    record.write_text(json.dumps(data | {"credit": "Map: Bundesarchiv"}))

    added = add_graphic(project, "arrow", 2.0, 4.0)

    assert any("Bundesarchiv" in w and "credit" in w for w in added.warnings)


def test_a_graphic_in_the_corner_a_credit_line_shows_in_is_warned_and_the_other_corner_is_not(
    project,
):
    side = values(project, "l-p1-2").get("align", "left")
    other = "right" if side == "left" else "left"

    same = add_graphic(project, "arrow", 2.0, 4.0, position=f"bottom-{side}")
    clear = add_graphic(project, "arrow", 2.0, 4.0, position=f"bottom-{other}")

    assert any("l-p1-2" in w and "credit-line" in w and f"bottom-{side}" in w for w in same.warnings)
    assert not any("credit-line" in w for w in clear.warnings)
    # a top corner is no credit line's
    assert not any("credit-line" in w for w in add_graphic(project, "arrow", 2.0, 4.0, position="top-left").warnings)


def test_a_date_stamp_takes_the_corner_it_names_and_bottom_left_when_it_names_none():
    def stamp(values):
        return Mount("<div>", LayerTiming("p1", 5, "date-stamp", 1.0, 5.0, 3, values), "")

    named = _corner_warnings("l-p1-9", "top-right", [stamp({"position": "top-right"})])
    assert len(named) == 1 and "l-p1-5 (date-stamp)" in named[0]
    assert _corner_warnings("l-p1-9", "top-left", [stamp({"position": "top-right"})]) == []
    assert len(_corner_warnings("l-p1-9", "bottom-left", [stamp({})])) == 1


def test_a_page_with_no_layers_is_refused(tmp_path):
    (tmp_path / "index.html").write_text('<div id="root" data-duration="10"></div>')
    (tmp_path / "assets/media/vectors").mkdir(parents=True)
    (tmp_path / "assets/media/vectors/arrow.svg").write_text(ARROW)

    with pytest.raises(EditError, match="no layers"):
        add_graphic(tmp_path, "arrow", 1.0, 3.0)


def test_a_graphic_that_goes_on_past_a_dissolve_out_of_its_shot_warns_it_is_gone_with_the_shot(
    tmp_path,
):
    raw = two_shots()
    raw["shots"][1].update(start=7.5, transition_in={"type": "dissolve", "seconds": 0.5})
    dissolving = composed(tmp_path, raw)[0]
    vectors.save_vector(dissolving, ARROW, "arrow")

    late = add_graphic(dissolving, "arrow", 6.0, 3.0)  # ends at 9 s, after p1 is gone
    with_the_shot = add_graphic(dissolving, "arrow", 5.0, 3.0)  # ends at 8 s, as p1's layers do

    (warning,) = late.warnings
    assert warning.startswith(f"{late.layer}: it ends at 9 s")
    assert "dissolves in from 7.5 s" in warning
    assert with_the_shot.warnings == []
