"""The edits of ``project_edit`` as a page in and a page out: what the video MCP server writes through the HyperFrames app
(``replace_media_in``) and the change of one layer's variable (``set_variable_in``)."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from hyperframes_mcp.motion import project_edit
from hyperframes_mcp.motion.project_edit import EditError
from tests.motion.test_project_edit import composed, picture, two_shots, values


@pytest.fixture
def project(tmp_path) -> Path:
    return composed(tmp_path, two_shots())[0]


def page_of(project: Path) -> str:
    return (project / "index.html").read_text(encoding="utf-8")


def mount_of(page: str, layer: str) -> dict[str, str]:
    tag = re.search(rf'<div [^>]*id="{layer}"[^>]*>', page).group(0)
    return {k: html.unescape(v) for k, v in re.findall(r'([\w-]+)="([^"]*)"', tag)}


def test_replacing_media_in_a_page_gives_the_new_page_and_leaves_the_file_alone(project, tmp_path):
    page = page_of(project)

    new_page, done = project_edit.replace_media_in(project, page, "l-p1-1", picture(tmp_path), credit="Bundesarchiv")

    assert page_of(project) == page
    assert "assets/media/edits/vault.png" in new_page and "Bundesarchiv" in new_page
    assert done.credit_layer == "l-p1-2"
    assert (project / "assets/media/edits/vault.png").is_file()


def test_the_same_file_placed_again_is_the_one_already_there(project, tmp_path):
    source = picture(tmp_path)

    project_edit.replace_media(project, "l-p1-1", source, credit="a")
    project_edit.replace_media(project, "l-p1-1", source, credit="a")

    assert values(project, "l-p1-1")["image"] == "assets/media/edits/vault.png"
    edits = project / "assets/media/edits"
    assert sorted(p.name for p in edits.iterdir()) == ["vault.png"]


def set_variable(project: Path, layer: str, name: str, value):
    return project_edit.set_variable_in(page_of(project), layer, name, value)


def test_a_variable_is_set_in_the_mount_and_the_timeline_label_follows_it(project):
    page = page_of(project)

    new_page, done = set_variable(project, "l-p1-2", "text", "Bundesarchiv, Bild 183")

    assert (done.layer, done.component, done.name) == ("l-p1-2", "credit-line", "text")
    assert done.old == "US Army Signal Corps, NARA 111-SC-204515"
    shown = mount_of(new_page, "l-p1-2")
    assert json.loads(shown["data-variable-values"])["text"] == "Bundesarchiv, Bild 183"
    assert shown["data-timeline-label"] == "p1 · Credit: Bundesarchiv, Bild 183"
    changed = [(a, b) for a, b in zip(page.splitlines(), new_page.splitlines()) if a != b]
    assert len(changed) == 1 and len(page.splitlines()) == len(new_page.splitlines())


@pytest.mark.parametrize(
    ("layer", "name", "value", "words"),
    [
        ("l-p1-2", "text", "x" * 141, "at most 140"),
        ("l-p1-2", "text", 5, "must be text"),
        ("l-p1-2", "align", "middle", "one of left, right"),
        ("l-p1-1", "from", "x" * 81, "at most 80"),
        ("l-p1-1", "scrim", "dark", "one of none, left, bottom"),
        ("l-p1-2", "colour", "red", "takes: align, text"),
        ("l-x9-1", "text", "x", "no layer 'l-x9-1'"),
    ],
)
def test_a_name_or_value_the_block_does_not_take_is_refused(project, layer, name, value, words):
    with pytest.raises(EditError, match=words):
        set_variable(project, layer, name, value)


def test_the_length_and_the_file_of_a_layer_are_not_set_here(project):
    with pytest.raises(EditError, match="edit_timeline"):
        set_variable(project, "l-p1-2", "durationSeconds", 3)
    with pytest.raises(EditError, match="replace_media"):
        set_variable(project, "l-p1-1", "image", "assets/media/photos/x.jpg")


def test_the_last_credit_line_of_a_picture_cannot_be_emptied(project):
    with pytest.raises(EditError, match=r"l-p1-1.*credit"):
        set_variable(project, "l-p1-2", "text", "   ")


def test_a_credit_may_be_emptied_while_another_line_credits_the_picture(project):
    page = page_of(project)
    line = re.search(r'<div [^>]*id="l-p1-2"[^>]*></div>', page).group(0)
    page = page.replace(line, line + line.replace('id="l-p1-2"', 'id="l-p1-3"'))

    new_page, _ = project_edit.set_variable_in(page, "l-p1-2", "text", "")

    assert json.loads(mount_of(new_page, "l-p1-2")["data-variable-values"])["text"] == ""


LONG_CREDIT = " ".join(["word"] * 25)  # needs 9.9 s on screen at reading pace; the fixture's credit slot is shorter


def test_a_credit_too_long_for_its_slot_is_set_but_the_result_warns_with_the_time_it_needs(project, tmp_path):
    # seen live in the app: the replaced picture brought a twenty-word credit, the block threw, no credit was drawn
    new_page, done = project_edit.replace_media_in(
        project, page_of(project), "l-p1-1", picture(tmp_path), credit=LONG_CREDIT
    )

    assert LONG_CREDIT in new_page
    (warning,) = done.warnings
    assert warning.startswith("l-p1-2 (credit-line): a slot of ")
    assert "shorter than the 9.90 s credit-line needs" in warning
    assert "give the layer at least 9.9 s or shorten the text" in warning


def test_a_credit_that_fits_its_slot_gives_no_warning(project, tmp_path):
    _, done = project_edit.replace_media_in(
        project, page_of(project), "l-p1-1", picture(tmp_path), credit="Bundesarchiv"
    )

    assert done.warnings == []


def test_a_text_too_long_for_its_slot_is_set_and_warned_about_but_a_short_one_is_not(
    project,
):
    _, long = set_variable(project, "l-p1-2", "text", LONG_CREDIT)
    _, short = set_variable(project, "l-p1-2", "text", "Bundesarchiv")

    (warning,) = long.warnings
    assert warning.startswith("l-p1-2 (credit-line): a slot of ")
    assert "9.9 s" in warning
    assert short.warnings == []


FOURTEEN_WORDS = (
    "U.S. National Archives and Records Administration, Wikimedia Commons. Public domain. Collection one two three"
)


def test_a_credit_that_needs_longer_than_its_layer_but_fits_inside_its_picture_lengthens_the_layer(project, tmp_path):
    page = page_of(project)
    short = project_edit.mounts(page)["l-p1-2"]  # a slot of 4.5 s, as a six-word credit gets in a real video
    page = project_edit.rewrite_mount(page, short, short.layer.values | {"durationSeconds": 4.5}, duration=4.5)
    before = mount_of(page, "l-p1-2")
    assert float(before["data-duration"]) == 4.5

    new_page, done = project_edit.replace_media_in(project, page, "l-p1-1", picture(tmp_path), credit=FOURTEEN_WORDS)

    after = mount_of(new_page, "l-p1-2")
    assert len(FOURTEEN_WORDS.split()) == 14
    assert float(after["data-duration"]) == pytest.approx(6.6)
    assert json.loads(after["data-variable-values"])["durationSeconds"] == 6.6
    assert after["data-start"] == before["data-start"]  # it starts where it did
    assert done.warnings == []
    (note,) = done.notes
    assert note.startswith("l-p1-2 (credit-line) was lengthened from ")
    assert "to 6.6 s" in note and "still ends inside l-p1-1" in note
    assert project_edit.slot_problems(project_edit.layers_from_page(new_page)) == []


def test_a_credit_that_fits_its_layer_leaves_the_length_alone(project, tmp_path):
    page = page_of(project)

    new_page, done = project_edit.replace_media_in(project, page, "l-p1-1", picture(tmp_path), credit="Bundesarchiv")

    assert done.notes == []
    assert mount_of(new_page, "l-p1-2")["data-duration"] == mount_of(page, "l-p1-2")["data-duration"]


def test_a_credit_too_long_for_even_its_picture_is_not_lengthened_past_it(project, tmp_path):
    page = page_of(project)

    new_page, done = project_edit.replace_media_in(project, page, "l-p1-1", picture(tmp_path), credit=LONG_CREDIT)

    assert done.notes == [] and len(done.warnings) == 1
    assert mount_of(new_page, "l-p1-2")["data-duration"] == mount_of(page, "l-p1-2")["data-duration"]


def short_picture(page: str) -> str:
    """The shape of the live case: a picture of 4.04 s whose credit starts 0.15 s in and has 3.89 s, so it ends with it."""
    found = project_edit.mounts(page)
    pic, credit = found["l-p1-1"], found["l-p1-2"]
    page = project_edit.rewrite_mount(page, pic, pic.layer.values | {"durationSeconds": 4.04}, duration=4.04)
    return project_edit.rewrite_mount(
        page,
        credit,
        credit.layer.values | {"durationSeconds": 3.89},
        duration=3.89,
        start=0.15,
    )


EIGHTEEN_WORDS = " ".join(["word"] * 18)  # needs 7.8 s; the picture lasts 8 s


def test_a_credit_that_cannot_end_later_starts_earlier_inside_its_picture(project, tmp_path):
    # the credit starts 0.3 s in and already ends with its picture, so 7.8 s only fit by starting at 0.2 s
    new_page, done = project_edit.replace_media_in(
        project, page_of(project), "l-p1-1", picture(tmp_path), credit=EIGHTEEN_WORDS
    )

    after = mount_of(new_page, "l-p1-2")
    assert float(after["data-start"]) == pytest.approx(0.2)
    assert float(after["data-duration"]) == pytest.approx(7.8)
    assert done.warnings == []
    (note,) = done.notes
    assert "l-p1-2 (credit-line) now starts at 0.2 s (it was 0.3 s)" in note
    assert "ends with l-p1-1" in note
    assert project_edit.slot_problems(project_edit.layers_from_page(new_page)) == []


def test_a_credit_that_runs_on_past_its_picture_is_not_pulled_back_inside_it(project, tmp_path):
    # the credit starts 0.15 s before the picture ends and goes on past it, so it may belong to what follows:
    # starting it earlier would end it with this picture and leave that one bare
    page = page_of(project)
    credit = project_edit.mounts(page)["l-p1-2"]
    page = project_edit.rewrite_mount(page, credit, credit.layer.values, duration=0.8, start=7.85)

    new_page, done = project_edit.replace_media_in(project, page, "l-p1-1", picture(tmp_path), credit=EIGHTEEN_WORDS)

    after = mount_of(new_page, "l-p1-2")
    assert float(after["data-start"]) == pytest.approx(7.85)
    assert float(after["data-duration"]) == pytest.approx(0.8)
    assert done.notes == [] and len(done.warnings) == 1


def test_a_credit_from_a_files_record_that_still_does_not_fit_is_shortened_and_says_so(project, tmp_path):
    page = short_picture(page_of(project))
    source = picture(
        tmp_path,
        "capture.jpg",
        sidecar={"credit": "U.S. National Archives and Records Administration, Wikimedia Commons. Public domain."},
    )

    new_page, done = project_edit.replace_media_in(project, page, "l-p1-1", source)

    after = mount_of(new_page, "l-p1-2")
    assert done.credit == "U.S. National Archives. Public domain."
    assert json.loads(after["data-variable-values"])["text"] == done.credit
    assert float(after["data-start"]) == pytest.approx(0.14)  # 3.9 s end with the picture
    assert float(after["data-start"]) + float(after["data-duration"]) == pytest.approx(4.04)
    assert done.warnings == []
    assert any("was shortened to 'U.S. National Archives. Public domain.'" in n for n in done.notes)
    assert any("the file's record keeps the full text" in n for n in done.notes)


def test_a_credit_the_person_gave_is_never_shortened_for_them(project, tmp_path):
    page = short_picture(page_of(project))
    wording = "U.S. National Archives and Records Administration, Wikimedia Commons. Public domain."

    _, done = project_edit.replace_media_in(project, page, "l-p1-1", picture(tmp_path), credit=wording)

    assert done.credit == wording and done.notes == []
    (warning,) = done.warnings
    assert warning.startswith("l-p1-2 (credit-line): a slot of 3.89 s")


# ----- add_block: any block that takes no file, as a new layer


ALL_COUNTER = {
    "from": 0,
    "to": 3,
    "prefix": "",
    "suffix": "",
    "decimals": 0,
    "label": "x",
}


def style_of(page: str, layer: str) -> str:
    return mount_of(page, layer)["style"]


def z_of(page: str, layer: str) -> int:
    return int(re.search(r"z-index:\s*(\d+)", style_of(page, layer))[1])


def test_a_counter_is_added_as_a_new_layer_of_the_shot_on_screen_with_its_options_defaulted(
    project,
):
    page = page_of(project)

    new_page, done = project_edit.add_block_in(
        project,
        page,
        "counter",
        2.0,
        5.4,
        {
            "from": 0,
            "to": 3,
            "prefix": "",
            "suffix": " feet",
            "decimals": 0,
            "label": "of brick",
        },
    )

    tag = mount_of(new_page, "l-p1-3")
    assert (done.layer, done.shot, done.component) == ("l-p1-3", "p1", "counter")
    assert (done.start, done.end) == (2.0, 7.4)
    assert tag["data-composition-id"] == "counter"
    assert (float(tag["data-start"]), float(tag["data-duration"])) == (2.0, 5.4)
    assert tag["data-timeline-label"] == "p1 · Counter: of brick"
    assert json.loads(tag["data-variable-values"]) == {
        "durationSeconds": 5.4,
        "from": 0,
        "to": 3,
        "prefix": "",
        "suffix": " feet",
        "decimals": 0,
        "label": "of brick",
        "thousandsSeparator": True,
    }
    # in front of every layer it shares time with
    assert z_of(new_page, "l-p1-3") > max(z_of(page, "l-p1-1"), z_of(page, "l-p1-2"))
    assert (project / "compositions/counter.html").is_file()
    assert done.warnings == []
    assert page_of(project) == page  # the caller writes the page


def test_a_block_added_at_a_time_in_the_second_shot_joins_that_shot(project):
    _, done = project_edit.add_block_in(project, page_of(project), "date-stamp", 9.0, 5.0, {"text": "May 1945"})

    assert done.layer == "l-m1-3" and done.shot == "m1"


def test_a_block_that_goes_on_past_a_dissolve_out_of_its_shot_warns_it_is_gone_with_the_shot(
    tmp_path,
):
    raw = two_shots()
    raw["shots"][1].update(start=7.5, transition_in={"type": "dissolve", "seconds": 0.5})
    dissolving = composed(tmp_path, raw)[0]

    _, late = project_edit.add_block_in(dissolving, page_of(dissolving), "counter", 4.0, 5.4, ALL_COUNTER)
    _, early = project_edit.add_block_in(dissolving, page_of(dissolving), "counter", 2.0, 5.4, ALL_COUNTER)
    # ending where the dissolve ends is where every layer of the shot ends
    _, with_the_shot = project_edit.add_block_in(dissolving, page_of(dissolving), "counter", 3.0, 5.0, ALL_COUNTER)

    (warning,) = late.warnings
    assert warning.startswith("l-p1-3: it ends at 9.4 s")
    assert "dissolves in from 7.5 s" in warning and "by 8 s" in warning
    assert early.warnings == [] and with_the_shot.warnings == []


@pytest.mark.parametrize(
    ("args", "values", "message"),
    [
        (("date-plate", 2.0, 5.0), {}, "no block 'date-plate'"),
        (("photo-shot", 2.0, 5.0), {}, "replace_media"),
        (("graphic-stamp", 2.0, 5.0), {}, "add_graphic"),
        (("counter", 2.0, 5.4), {"colour": "red"}, "counter takes:"),
        (("counter", 2.0, 5.4), {"decimals": 9}, "maximum is 4"),
        (
            ("counter", 2.0, 5.4),
            {"to": 3},
            "counter: give from, prefix, suffix, decimals, label",
        ),
        (("credit-line", 2.0, 5.0), {}, "credit-line: give text"),
        (("date-stamp", 2.0, 5.0), {}, "never goes on screen"),
        (("counter", 2.0, 5.4), {"durationSeconds": 6}, "pass the length as duration"),
        (("counter", 2.0, 2.0), ALL_COUNTER, "at least 5 s"),
        (("counter", 2.0, 30.0), ALL_COUNTER, "at most 16 s"),
        (("counter", 25.0, 5.4), ALL_COUNTER, "past the end"),
        (("counter", -1.0, 5.4), ALL_COUNTER, "before the start"),
    ],
)
def test_a_block_that_cannot_be_added_is_refused_in_words_and_the_page_is_untouched(project, args, values, message):
    with pytest.raises(EditError, match=message):
        project_edit.add_block_in(project, page_of(project), *args, values)


def test_a_credit_text_too_long_for_the_slot_given_is_added_and_warned_about(project):
    _, done = project_edit.add_block_in(
        project,
        page_of(project),
        "credit-line",
        1.0,
        3.0,
        {"text": " ".join(["word"] * 25)},
    )

    (warning,) = done.warnings
    assert warning.startswith("l-p1-3 (credit-line): a slot of 3 s")


def test_a_block_that_covers_the_frame_warns_about_what_it_now_hides(project):
    _, done = project_edit.add_block_in(
        project,
        page_of(project),
        "act-title",
        1.0,
        6.0,
        {"title": "Part one", "kicker": "", "subtitle": ""},
    )

    assert any("l-p1-1" in w and "hidden" in w for w in done.warnings), done.warnings


# ----- arrange_layer_in: move and scale a layer by its mount's own style


def test_a_layer_placed_in_a_form_the_tool_does_not_read_is_refused_not_overwritten(
    project,
):
    page = page_of(project)
    tag = project_edit.mounts(page)["l-p1-2"].tag
    style = re.search(r'style="([^"]*)"', tag)[1]
    for odd in ("translate: 10px", "translate: 5% 2%", "scale: 120%"):
        placed = project_edit.set_attributes(page, tag, {"style": f"{style}; {odd}"})

        with pytest.raises(EditError, match="does not read"):
            project_edit.arrange_layer_in(placed, "l-p1-2", dx=5)

        cleared, done = project_edit.arrange_layer_in(placed, "l-p1-2", reset=True)

        assert done.now == {
            "dx": 0,
            "dy": 0,
            "scale": 1,
        }  # a reset needs to read nothing
        assert "translate" not in style_of(cleared, "l-p1-2")


def test_a_layer_is_moved_and_scaled_by_its_mount_and_reset_puts_it_back(project):
    page = page_of(project)

    moved, done = project_edit.arrange_layer_in(page, "l-p1-2", dx=0, dy=200, scale=0.8)

    style = style_of(moved, "l-p1-2")
    assert "translate: 0px 200px" in style and "scale: 0.8" in style
    assert re.search(r"z-index:\s*\d+", style)  # the stacking is kept
    assert done.was == {"dx": 0, "dy": 0, "scale": 1}
    assert done.now == {"dx": 0, "dy": 200, "scale": 0.8}
    assert page_of(project) == page
    back, undone = project_edit.arrange_layer_in(moved, "l-p1-2", reset=True)
    assert "translate" not in style_of(back, "l-p1-2")
    assert "scale" not in style_of(back, "l-p1-2")
    assert undone.now == {"dx": 0, "dy": 0, "scale": 1}
    assert back == page  # as it was, byte for byte


def test_only_the_parts_given_change_and_the_rest_stays(project):
    moved, _ = project_edit.arrange_layer_in(page_of(project), "l-p1-2", dx=30, dy=200, scale=0.8)

    nudged, done = project_edit.arrange_layer_in(moved, "l-p1-2", dx=-50)

    style = style_of(nudged, "l-p1-2")
    assert "translate: -50px 200px" in style and "scale: 0.8" in style
    assert done.was == {"dx": 30, "dy": 200, "scale": 0.8}


@pytest.mark.parametrize(
    ("layer", "arguments", "message"),
    [
        ("l-p9-1", {"dx": 5}, "no layer 'l-p9-1'"),
        ("l-p1-2", {}, "say what to change"),
        ("l-p1-2", {"dx": 5000}, "at most 1920 px"),
        ("l-p1-2", {"dy": -3000}, "at most 1080 px"),
        ("l-p1-2", {"scale": 5}, "between 0.2 and 2"),
        ("l-p1-1", {"dx": 5}, "fills the frame"),
    ],
)
def test_a_move_that_makes_no_sense_is_refused(project, layer, arguments, message):
    with pytest.raises(EditError, match=message):
        project_edit.arrange_layer_in(page_of(project), layer, **arguments)


def test_a_move_that_leaves_the_layer_where_it_is_is_refused_so_no_history_entry_is_spent(
    project,
):
    page = page_of(project)
    moved, _ = project_edit.arrange_layer_in(page, "l-p1-2", dx=30, scale=0.8)

    for unchanged in ({"reset": True}, {"dx": 0}, {"scale": 1}):
        with pytest.raises(EditError, match="already there.*nothing to change"):
            project_edit.arrange_layer_in(page, "l-p1-2", **unchanged)
    with pytest.raises(EditError, match="dx 30, dy 0, scale 0.8"):
        project_edit.arrange_layer_in(moved, "l-p1-2", dx=30, scale=0.8)


def test_putting_the_same_file_in_a_layer_again_says_set_variable_changes_only_the_credit(project, tmp_path):
    page, _ = project_edit.replace_media_in(
        project, page_of(project), "l-p1-1", picture(tmp_path), credit="Bundesarchiv"
    )
    placed = project / "assets/media/edits/vault.png"

    _, again = project_edit.replace_media_in(project, page, "l-p1-1", placed, credit="Bundesarchiv, Bild 183")

    (note,) = again.notes
    assert "already in l-p1-1" in note and "set_variable" in note and "l-p1-2" in note
