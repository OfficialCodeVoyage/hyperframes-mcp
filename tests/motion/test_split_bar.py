"""The ``split-bar`` block: one whole bar split into labelled parts, with brackets for ranges on the same scale.

The static checks keep the promises of the contract. The logic checks run the block's own functions in Node
(``tests/motion/node_logic.py``): the shape of the variables and the time plan with the shortest slot. The geometry has
its own file (``test_split_bar_layout.py``). The page checks run the whole script against the stub document of
``tests/motion/block_harness.py``.
"""

from __future__ import annotations

import json
import re

import pytest

from hyperframes_mcp.motion import registry
from tests.motion import block_helpers, node_logic
from tests.motion.bars_helpers import (
    SAFE_W,
    D,
    js_round,
    page_minimum,
    reading,
    texts,
    tweens_on,
    with_class,
)
from tests.motion.block_harness import (
    ENTER,
    EXIT,
    T,
    block_path,
    check_calm,
    check_max_lengths,
    check_mounted,
    check_static,
    code_of,
    declared_variables,
    defaults,
    html,
    run_block,
)
from tests.motion.node_logic import LogicError, call, needs_node
from tests.motion.split_bar_helpers import (
    BLOCK,
    MARKER,
    NAME,
    PARTS,
    content,
    marker,
    part,
    variables,
)


def minimum(**kw) -> float:
    return page_minimum(BLOCK, variables(**kw))


# -- static ---------------------------------------------------------------------------------------


def test_it_declares_its_variables_durationseconds_first_and_is_our_own():
    """That the registry repeats the declaration, lints clean and has the root duration is checked for every item."""
    assert [v["id"] for v in block_helpers.declared(BLOCK)] == [
        "durationSeconds",
        "wholeLabel",
        "wholeValue",
        "parts",
        "whole",
        "markers",
        "note",
    ]
    assert registry.get(NAME).source is None
    assert registry.get(NAME).type == "hyperframes:block"


def test_it_registers_one_paused_timeline_and_tweens_only_with_fromto_and_no_literal_colour():
    check_static(NAME)


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    check_mounted(NAME)


def test_the_longest_text_the_variables_allow_is_the_one_the_script_enforces():
    check_max_lengths(
        NAME,
        {"wholeLabel": "MAX_LABEL", "wholeValue": "MAX_DISPLAY", "note": "MAX_NOTE"},
    )


def test_a_part_grows_by_a_left_to_right_clip_path_never_by_a_width_tween_or_timers():
    text = code_of(html(NAME))

    assert 'clipPath: "inset(0% 100% 0% 0%)"' in text
    assert 'clipPath: "inset(0% 0% 0% 0%)"' in text
    assert not re.search(r"\bstagger\s*:", text)
    assert "steps(" not in text
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text


@pytest.mark.parametrize("name", [NAME, "compare-bars"])
def test_the_canvas_the_logic_assumes_is_the_canvas_of_the_tokens(name):
    """Both bar blocks lay out inside their logic, which has no DOM to ask, so each carries the canvas as constants."""
    logic = node_logic.logic_of(block_path(name))

    assert int(re.search(r"var CANVAS_W = (\d+);", logic).group(1)) == T.canvas["width"]
    assert int(re.search(r"var CANVAS_H = (\d+);", logic).group(1)) == T.canvas["height"]
    root = re.search(r'data-width="(\d+)"\s+data-height="(\d+)"', html(name))
    assert (int(root.group(1)), int(root.group(2))) == (
        T.canvas["width"],
        T.canvas["height"],
    )


@pytest.mark.parametrize("colour", ["paper", "gold", "loss", "paper_aged"])
def test_every_text_colour_reads_over_a_white_frame_under_the_backing(colour):
    """The backing is the scrim token at its CSS opacity; over pure white, the worst frame, every text colour passes."""
    assert block_helpers.scrim_contrast(html(NAME), colour) >= T.min_ratio
    assert f"var(--color-{colour.replace('_', '-')})" in html(NAME)


def test_the_tones_are_the_four_the_stylesheet_knows():
    text = html(NAME)

    for tone in ("gold", "paper", "ember", "mute"):
        assert f".sb-tone-{tone} {{" in text
    assert re.search(r"var TONES = \[\"gold\", \"paper\", \"ember\", \"mute\"\];", text)


# -- the variables --------------------------------------------------------------------------------


@needs_node
def test_a_list_variable_is_parsed_passed_through_or_empty():
    assert call(BLOCK, "parseList", '[{"a": 1}]', "parts") == [{"a": 1}]
    assert call(BLOCK, "parseList", [{"a": 1}], "parts") == [{"a": 1}]
    assert call(BLOCK, "parseList", "", "markers") == []
    assert call(BLOCK, "parseList", "[]", "markers") == []
    with pytest.raises(LogicError, match="parts variable is not valid JSON"):
        call(BLOCK, "parseList", "[{oops", "parts")
    with pytest.raises(LogicError, match="markers variable is missing"):
        call(BLOCK, "parseList", None, "markers")


@needs_node
def test_the_variables_become_the_checked_content():
    out = content(
        [part(label="  went abroad ", value=503, tone="ember", at=1.5, seconds=2)],
        [marker(from_=10, to=210, at=4)],
        note="  The ledger's own figures  ",
    )

    assert out["wholeLabel"] == "The Bundesbank gold ledger"
    assert out["whole"] == 791
    assert out["parts"] == [
        {
            "label": "went abroad",
            "display": "some",
            "value": 503,
            "tone": "ember",
            "at": 1.5,
            "seconds": 2,
        }
    ]
    assert out["markers"][0]["from"] == 10 and out["markers"][0]["to"] == 210
    assert out["note"] == "The ledger's own figures"


@needs_node
def test_markers_may_be_an_empty_list_or_empty_text():
    assert content(markers=[])["markers"] == []
    assert call(BLOCK, "readContent", variables(markers=[]) | {"markers": ""})["markers"] == []


@needs_node
@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"wholeLabel": ""}, "the wholeLabel variable is empty"),
        ({"wholeLabel": "x" * 35}, "wholeLabel variable is 35 characters; at most 34"),
        ({"wholeValue": "x" * 25}, "wholeValue variable is 25 characters; at most 24"),
        ({"wholeValue": None}, "the wholeValue variable is missing"),
        ({"whole": 0}, "whole variable must be greater than 0"),
        ({"whole": "791"}, "whole variable is missing or is not a number"),
        ({"note": "x" * 71}, "note variable is 71 characters; at most 70"),
        ({"parts": "[{oops"}, "parts variable is not valid JSON"),
        ({"parts": "[]"}, "parts variable must be a list of 1 to 6 parts"),
        ({"parts": '{"a": 1}'}, "parts variable must be a list of 1 to 6 parts"),
        ({"markers": "{nope"}, "markers variable is not valid JSON"),
        ({"markers": '{"a": 1}'}, "markers variable must be a list of up to 3"),
    ],
)
def test_a_missing_or_wrong_variable_throws_naming_it(over, message):
    with pytest.raises(LogicError, match=message):
        call(BLOCK, "readContent", variables() | over)


@needs_node
@pytest.mark.parametrize(
    ("parts", "message"),
    [
        ([part(label="x" * 35)], r"parts\[0\]\.label is 35 characters; at most 34"),
        ([part(display="x" * 25)], r"parts\[0\]\.display is 25 characters; at most 24"),
        ([part(label="")], r"parts\[0\]\.label is empty"),
        ([part(display="  ")], r"parts\[0\]\.display is empty"),
        ([part(), part(value=0)], r"parts\[1\]\.value must be greater than 0"),
        ([part(value=-5)], r"parts\[0\]\.value must be greater than 0"),
        ([part(value="9")], r"parts\[0\]\.value is missing or is not a number"),
        ([part(tone="red")], r'parts\[0\]\.tone "red" is not one of gold, paper'),
        ([part(tone=None)], r"parts\[0\]\.tone"),
        ([part(at=-0.5)], r"parts\[0\]\.at is negative"),
        ([part(seconds=0)], r"parts\[0\]\.seconds must be greater than 0"),
        ([part(seconds=-1)], r"parts\[0\]\.seconds must be greater than 0"),
        ([{"label": "a"}], r"parts\[0\]\.display"),
        (["nope"], r"parts\[0\] must be an object"),
        (
            [part(value=500), part(value=300)],
            "parts add up to 800.*more than the whole",
        ),
        ([part(value=100), part(value=5)], r"parts\[1\] is under 1 percent"),
        ([part()] * 7, "list of 1 to 6 parts"),
    ],
)
def test_a_part_that_cannot_be_drawn_throws_naming_the_part_and_the_field(parts, message):
    with pytest.raises(LogicError, match=message):
        call(BLOCK, "readContent", variables(parts=parts))


@needs_node
@pytest.mark.parametrize(
    ("markers", "message"),
    [
        ([marker(label="x" * 35)], r"markers\[0\]\.label is 35 characters"),
        (
            [marker(display="x" * 35)],
            r"markers\[0\]\.display is 35 characters; at most 34",
        ),
        ([marker(from_=-1)], r"markers\[0\]\.from is below 0"),
        ([marker(from_=700, to=792)], r"markers\[0\]\.to \(792\) is past the whole"),
        ([marker(from_=50, to=50)], r"markers\[0\]\.from must be less than"),
        ([marker(from_=60, to=50)], r"markers\[0\]\.from must be less than"),
        ([marker(from_=0, to=3)], r"markers\[0\] is under 1 percent"),
        ([marker(at=-1)], r"markers\[0\]\.at is negative"),
        ([marker(from_="a")], r"markers\[0\]\.from is missing or is not a number"),
        ([marker()] * 4, "list of up to 3"),
    ],
)
def test_a_marker_that_cannot_be_drawn_throws_naming_the_marker_and_the_field(markers, message):
    with pytest.raises(LogicError, match=message):
        call(BLOCK, "readContent", variables(markers=markers))


@needs_node
def test_parts_that_fill_the_whole_exactly_are_fine():
    assert content([part(value=400), part(value=391)], [])["parts"][1]["value"] == 391


# -- the time plan --------------------------------------------------------------------------------


@needs_node
def test_the_shortest_slot_is_the_last_part_growing_its_label_arriving_the_reading_and_the_exit():
    one = [part("went abroad", "about two thirds", 503, "ember", at=1.0, seconds=1.2)]

    got = page_minimum(BLOCK, variables(one, []))

    # 1.0 + 1.2 growing, 0.7 for the label to arrive, 1.2 + 0.3 a word of its 5 words, 0.5 out
    assert got == pytest.approx(1.0 + 1.2 + ENTER + reading(5) + EXIT)
    assert got == pytest.approx(6.1)


@needs_node
def test_the_shortest_slot_of_the_default_frame_is_the_marker_or_the_last_part_whichever_ends_later():
    d = defaults(NAME)
    parts, markers = json.loads(d["parts"]), json.loads(d["markers"])
    last = parts[-1]
    last_end = last["at"] + last["seconds"] + ENTER + reading(len(f"{last['label']} {last['display']}".split()))
    marker_end = max(m["at"] + ENTER + reading(len(f"{m['label']} {m['display']}".split())) for m in markers)

    got = page_minimum(BLOCK, d)

    assert got == pytest.approx(max(last_end, marker_end) + EXIT)
    assert got == pytest.approx(10.1)


@needs_node
def test_a_marker_that_arrives_last_decides_the_slot():
    got = page_minimum(
        BLOCK,
        variables(
            [part(at=0, seconds=1)],
            [marker("the pile", "about a quarter of the ledger", 0, 210, at=9.0)],
        ),
    )

    assert got == pytest.approx(9.0 + ENTER + reading(2 + 6) + EXIT)


@needs_node
def test_the_heading_and_the_note_are_read_too_so_a_long_one_can_decide_the_slot():
    quick = [part("a", "b", 100, at=0, seconds=0.3)]
    note = "one two three four five six seven eight nine ten eleven twelve"
    head = ENTER + reading(len(["The", "Bundesbank", "gold", "ledger", "about", "791", "tonnes"])) + EXIT

    assert page_minimum(BLOCK, variables(quick, [], note=note)) == pytest.approx(ENTER + reading(12) + EXIT)
    assert page_minimum(BLOCK, variables(quick, [])) == pytest.approx(max(head, 0.3 + ENTER + reading(2) + EXIT))


@needs_node
def test_parts_may_grow_at_the_same_time_or_overlapping():
    got = minimum(
        parts=[part(at=1, seconds=2), part(at=1, seconds=1), part(at=2, seconds=3)],
        markers=[],
    )

    assert got == pytest.approx(5.0 + ENTER + reading(3) + EXIT)


@needs_node
@pytest.mark.parametrize(
    ("parts", "message"),
    [
        (
            [part(seconds=0.2)],
            r"parts\[0\]\.seconds is 0.2 s; a part needs at least 0.3 s",
        ),
        (
            [part(at=3), part(at=2)],
            r"parts\[1\]\.at is earlier than parts\[0\]\.at: parts grow left to right",
        ),
    ],
)
def test_a_time_plan_that_cannot_be_drawn_throws_naming_the_part(parts, message):
    with pytest.raises(LogicError, match=message):
        minimum(parts=parts, markers=[])


@needs_node
def test_a_layout_that_cannot_be_drawn_is_refused_by_minslot_too_so_the_preflight_sees_it():
    crowded = [part(f"label {i}", "about", 20 if i else 30, "gold", at=i) for i in range(4)]

    with pytest.raises(LogicError, match="no free place"):
        minimum(parts=crowded, markers=[])


@needs_node
def test_the_declared_floor_is_the_shortest_slot_any_legal_variables_need():
    smallest = page_minimum(
        BLOCK,
        variables(
            [part("a", "b", 100, at=0, seconds=D["micro"])],
            [],
            wholeLabel="a",
            wholeValue="b",
        ),
    )
    slot = declared_variables(NAME)["durationSeconds"]

    assert slot["min"] == pytest.approx(smallest, abs=0.051)
    assert slot["min"] <= smallest + 1e-9
    assert slot["min"] <= slot["default"] <= slot["max"]
    assert slot["default"] >= page_minimum(BLOCK, defaults(NAME)) - 1e-9


# -- the page, run in Node against a stub document ------------------------------------------------


def test_the_default_slot_builds_one_calm_timeline_that_fills_the_slot():
    run = run_block(NAME)

    check_calm(run, NAME, defaults(NAME)["durationSeconds"])


def test_every_text_drawn_is_a_variables_text_and_no_number_is_made_up():
    v = variables(note="The ledger's own figures")
    run = run_block(NAME, v | {"durationSeconds": 20})

    assert run.error is None, run.error
    expected = {
        v["wholeLabel"],
        v["wholeValue"],
        v["note"],
        *(p["label"] for p in PARTS),
        *(p["display"] for p in PARTS),
        MARKER["label"],
        MARKER["display"],
    }
    assert sorted(texts(run)) == sorted(expected)


def test_text_is_set_trimmed_through_textcontent_and_never_as_markup():
    run = run_block(
        NAME,
        variables(wholeLabel=" <b>the</b> ledger ", wholeValue="A & B") | {"durationSeconds": 20},
    )

    assert run.error is None
    assert "<b>the</b> ledger" in texts(run)
    assert "A & B" in texts(run)


def test_each_part_grows_at_its_own_time_left_to_right_and_its_flag_arrives_when_it_has_grown():
    run = run_block(NAME, variables() | {"durationSeconds": 20})
    wipes = tweens_on(run, "sb-part")
    flags = tweens_on(run, "sb-flag-text")
    poles = tweens_on(run, "sb-pole")

    assert all(t.frm == {"clipPath": "inset(0% 100% 0% 0%)"} for t in wipes)
    assert [(t.start, t.duration) for t in wipes] == [(1.0, 1.2), (2.6, 1.6)]
    assert all(t.ease == T.ease["enter"] for t in wipes)
    assert [t.start for t in flags] == pytest.approx([1.0 + 1.2, 2.6 + 1.6])
    assert [t.start for t in poles] == [t.start for t in flags]
    assert all(t.duration == ENTER for t in (*flags, *poles))


def test_a_marker_is_drawn_from_its_left_end_with_its_label_at_its_time():
    run = run_block(NAME, variables() | {"durationSeconds": 20})
    (bracket,) = tweens_on(run, "sb-bracket")
    (text,) = tweens_on(run, "sb-marker-text")

    assert bracket.frm == {"clipPath": "inset(0% 100% 0% 0%)"}
    assert bracket.start == text.start == 5.0
    assert bracket.duration == text.duration == ENTER


def test_the_parts_are_placed_on_the_scale_by_inline_geometry_not_by_tweened_layout():
    run = run_block(NAME, variables() | {"durationSeconds": 20})
    parts = with_class(run, "sb-part")

    assert [p["style"]["left"] for p in parts] == [
        "0px",
        f"{js_round(236 / 791 * SAFE_W)}px",
    ]
    assert [p["style"]["width"] for p in parts] == [
        f"{js_round(236 / 791 * SAFE_W)}px",
        f"{js_round(739 / 791 * SAFE_W) - js_round(236 / 791 * SAFE_W)}px",
    ]
    assert all(set(t.frm) <= {"opacity", "y", "clipPath"} for t in run.tweens)


@pytest.mark.parametrize("tone", ["gold", "paper", "ember", "mute"])
def test_every_tone_is_a_class_the_page_puts_on_the_part_and_its_flag(tone):
    run = run_block(NAME, variables([part(tone=tone)], []) | {"durationSeconds": 12})

    assert run.error is None
    assert [p["className"] for p in with_class(run, "sb-part")] == [f"sb-part sb-tone-{tone}"]
    assert [p["className"] for p in with_class(run, "sb-flag")] == [f"sb-flag sb-tone-{tone}"]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"parts": "[{oops"}, "parts variable is not valid JSON"),
        ({"markers": "{oops"}, "markers variable is not valid JSON"),
        ({"parts": json.dumps([part(tone="red")])}, r"parts\[0\]\.tone"),
        ({"whole": 100}, "more than the whole variable"),
        ({"wholeLabel": "x" * 35}, "wholeLabel variable is 35 characters"),
        ({"durationSeconds": "soon"}, "shorter than"),
    ],
)
def test_a_wrong_variable_fails_the_render_naming_it_and_registers_no_timeline(override, message):
    run = run_block(NAME, variables() | {"durationSeconds": 12} | override)

    assert re.search(message, run.error), run.error
    assert run.registered == []


def test_a_part_that_would_end_after_the_slot_throws():
    run = run_block(NAME, variables([part(at=8, seconds=3)], []) | {"durationSeconds": 10})

    assert "shorter than" in run.error
