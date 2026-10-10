"""The ``compare-bars`` block: two to four horizontal bars on one scale, each with its label and display.

The static checks keep the promises of the contract. The logic checks run the block's own functions in Node
(``tests/motion/node_logic.py``): the shape of the variables, the geometry (every bar on one scale, its text inside it
or beside it), the time plan and the shortest slot. The page checks run the whole script against the stub document of
``tests/motion/block_harness.py``. The advance table is checked in ``test_split_bar_layout.py``; this block carries a
copy of it, which ``test_the_advance_table_is_the_one_of_split_bar`` ties to the first.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from typing import Any

import pytest

from hyperframes_mcp.motion import registry, tokens
from tests.motion import block_helpers, node_logic
from tests.motion.bars_helpers import (
    SAFE_H,
    SAFE_W,
    D,
    js_round,
    layout_of,
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
    blend,
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

NAME = "compare-bars"
BLOCK = block_path(NAME)

AMERICA = {
    "label": "America's gold, 1945",
    "display": "about 20,000 tonnes",
    "value": 20000,
    "tone": "paper",
    "at": 1.0,
    "seconds": 1.4,
}
LEDGER = {
    "label": "The whole Bundesbank ledger",
    "display": "about 791 tonnes",
    "value": 791,
    "tone": "gold",
    "at": 2.8,
    "seconds": 1.2,
}
SWISS = {
    "label": "Swiss banks took in",
    "display": "about 395 tonnes",
    "value": 395,
    "tone": "ember",
    "at": 1.0,
    "seconds": 1.4,
}
MARBURG = {
    "label": "Marburg pile, Bundesbank's figure",
    "display": "about 210 tonnes",
    "value": 210,
    "tone": "gold",
    "at": 2.8,
    "seconds": 1.2,
}


def bar(label="a bar", display="some", value=10000, tone="gold", at=0.0, seconds=1.0):
    return {
        "label": label,
        "display": display,
        "value": value,
        "tone": tone,
        "at": at,
        "seconds": seconds,
    }


def variables(bars=None, scale_max=20000, **over: Any) -> dict[str, Any]:
    base = {
        "title": "Gold, in tonnes",
        "bars": json.dumps([AMERICA, LEDGER] if bars is None else bars),
        "scaleMax": scale_max,
        "note": "",
    }
    return base | over


def content(bars=None, scale_max=20000, **over: Any) -> dict[str, Any]:
    return call(BLOCK, "readContent", variables(bars, scale_max, **over))


def layout(bars=None, scale_max=20000, **over: Any) -> dict[str, Any]:
    return layout_of(BLOCK, variables(bars, scale_max, **over))


def minimum(*args: Any, **kw: Any) -> float:
    return page_minimum(BLOCK, variables(*args, **kw))


# -- static ---------------------------------------------------------------------------------------


def test_it_declares_its_variables_durationseconds_first_and_is_our_own():
    assert [v["id"] for v in block_helpers.declared(BLOCK)] == [
        "durationSeconds",
        "title",
        "bars",
        "scaleMax",
        "note",
    ]
    assert registry.get(NAME).source is None
    assert registry.get(NAME).type == "hyperframes:block"


def test_it_registers_one_paused_timeline_and_tweens_only_with_fromto_and_no_literal_colour():
    check_static(NAME)


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    check_mounted(NAME)


def test_the_longest_text_the_variables_allow_is_the_one_the_script_enforces():
    check_max_lengths(NAME, {"title": "MAX_TITLE", "note": "MAX_NOTE"})


def test_a_bar_grows_by_a_left_to_right_clip_path_never_by_a_width_tween():
    text = code_of(html(NAME))

    assert 'clipPath: "inset(0% 100% 0% 0%)"' in text
    assert 'clipPath: "inset(0% 0% 0% 0%)"' in text
    assert not re.search(r"\bstagger\s*:", text)
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text


@pytest.mark.parametrize("colour", ["paper", "gold", "loss", "paper_aged"])
def test_every_text_colour_beside_a_bar_reads_over_a_white_frame_under_the_backing(
    colour,
):
    assert block_helpers.scrim_contrast(html(NAME), colour) >= T.min_ratio


@pytest.mark.parametrize("tone", ["gold", "paper", "ember", "mute"])
def test_text_inside_a_bar_reads_on_the_bars_fill(tone):
    """Ink on gold, paper and the lifted oxblood; paper on the dark muted fill (paper-aged at 38 percent over night)."""
    fills = {
        "gold": T.colors["gold"],
        "paper": T.colors["paper"],
        "ember": T.colors["loss"],
        "mute": blend(T.colors["paper_aged"], T.colors["night"], 0.38),
    }
    on = T.colors["paper"] if tone == "mute" else T.colors["ink"]

    assert tokens.contrast(on, fills[tone]) >= T.min_ratio
    assert f".cb-tone-{tone} {{" in html(NAME)


def test_the_advance_table_is_the_one_of_split_bar():
    def table(name: str) -> str:
        text = node_logic.logic_of(block_path(name))
        return re.search(r"var ADVANCE = \[(.*?)\];", text, re.DOTALL).group(1) + re.search(
            r"var ADVANCE_OTHER = \{(.*?)\};", text, re.DOTALL
        ).group(1)

    assert re.sub(r"\s+", "", table(NAME)) == re.sub(r"\s+", "", table("split-bar"))


# -- the variables --------------------------------------------------------------------------------


@needs_node
def test_the_variables_become_the_checked_content():
    out = content(
        [bar(label="  America  ", value=20000, tone="paper", at=1, seconds=2), LEDGER],
        note=" a note ",
    )

    assert out["title"] == "Gold, in tonnes"
    assert out["scaleMax"] == 20000
    assert out["bars"][0] == {
        "label": "America",
        "display": "some",
        "value": 20000,
        "tone": "paper",
        "at": 1,
        "seconds": 2,
    }
    assert out["note"] == "a note"


@needs_node
@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"title": ""}, "the title variable is empty"),
        ({"title": "x" * 41}, "title variable is 41 characters; at most 40"),
        ({"scaleMax": 0}, "scaleMax variable must be greater than 0"),
        ({"scaleMax": None}, "scaleMax variable is missing or is not a number"),
        ({"note": "x" * 71}, "note variable is 71 characters; at most 70"),
        ({"bars": "[{oops"}, "bars variable is not valid JSON"),
        ({"bars": "[]"}, "bars variable must be a list of 2 to 4 bars"),
        ({"bars": '{"a": 1}'}, "bars variable must be a list of 2 to 4 bars"),
    ],
)
def test_a_missing_or_wrong_variable_throws_naming_it(over, message):
    with pytest.raises(LogicError, match=message):
        call(BLOCK, "readContent", variables() | over)


@needs_node
@pytest.mark.parametrize(
    ("bars", "message"),
    [
        ([bar()], "list of 2 to 4 bars"),
        ([bar()] * 5, "list of 2 to 4 bars"),
        (
            [bar(), bar(label="x" * 35)],
            r"bars\[1\]\.label is 35 characters; at most 34",
        ),
        (
            [bar(display="x" * 25), bar()],
            r"bars\[0\]\.display is 25 characters; at most 24",
        ),
        ([bar(label=""), bar()], r"bars\[0\]\.label is empty"),
        ([bar(), bar(value=0)], r"bars\[1\]\.value must be greater than 0"),
        ([bar(value="9"), bar()], r"bars\[0\]\.value is missing or is not a number"),
        (
            [bar(value=20001), bar()],
            r"bars\[0\]\.value \(20001\) is over scaleMax \(20000\)",
        ),
        ([bar(), bar(value=100)], r"bars\[1\] is under 1 percent of scaleMax"),
        ([bar(tone="red"), bar()], r'bars\[0\]\.tone "red" is not one of gold, paper'),
        ([bar(at=-1), bar()], r"bars\[0\]\.at is negative"),
        ([bar(seconds=0), bar()], r"bars\[0\]\.seconds must be greater than 0"),
        ([bar(seconds=-2), bar()], r"bars\[0\]\.seconds must be greater than 0"),
        (["nope", bar()], r"bars\[0\] must be an object"),
        ([{"label": "a", "display": "b"}, bar()], r"bars\[0\]\.value"),
    ],
)
def test_a_bar_that_cannot_be_drawn_throws_naming_the_bar_and_the_field(bars, message):
    with pytest.raises(LogicError, match=message):
        call(BLOCK, "readContent", variables(bars))


@needs_node
def test_the_largest_bar_may_be_exactly_the_scale():
    assert content([bar(value=20000), bar(value=10000)])["bars"][0]["value"] == 20000


# -- the geometry ---------------------------------------------------------------------------------


@needs_node
def test_every_bar_is_its_share_of_the_safe_width_on_one_scale_from_the_left():
    out = layout()
    big, small = out["bars"]

    assert big["width"] == SAFE_W
    assert small["width"] == js_round(791 / 20000 * SAFE_W) == 68
    assert out["width"] == SAFE_W and out["stackX"] == T.safe["left"]


@needs_node
def test_a_bar_of_four_percent_is_a_sliver_with_its_text_beside_it_a_long_one_holds_it_inside():
    out = layout()
    big, small = out["bars"]

    assert big["inside"] and big["textLeft"] == 28
    assert not small["inside"]
    assert small["textLeft"] == small["width"] + 28
    assert small["textLeft"] + small["textW"] <= SAFE_W
    assert 60 < small["width"] < 80


@needs_node
def test_the_second_example_a_long_bar_and_one_a_little_over_half_the_scale():
    out = layout([SWISS, MARBURG], 400)
    long_, half = out["bars"]

    assert long_["width"] == js_round(395 / 400 * SAFE_W)
    assert half["width"] == js_round(210 / 400 * SAFE_W)
    assert long_["inside"]
    # the text of the half-length bar is 33 characters: it is inside when it fits with its margins, else beside the bar
    assert half["inside"] == (half["width"] >= half["textW"] + 2 * 28)
    assert half["textLeft"] + half["textW"] <= SAFE_W


@needs_node
def test_a_text_that_fits_neither_inside_nor_beside_its_bar_is_refused_naming_the_bar():
    wide = bar("W" * 34, "W" * 24, 10000, "gold")

    with pytest.raises(
        LogicError,
        match=r"text of bars\[1\] .* fits neither inside its bar nor beside it",
    ):
        layout([bar(), wide])


@needs_node
def test_text_is_inside_when_it_fits_with_a_margin_and_beside_when_it_does_not():
    for value, inside in ((1000, False), (20000, True)):
        out = layout([bar("a label", "a display", value), bar(value=20000)])
        assert out["bars"][0]["inside"] is inside, value


@needs_node
def test_the_bars_are_stacked_in_order_below_the_title_with_equal_rows():
    out = layout([AMERICA, LEDGER, SWISS, MARBURG][:4], 20000)
    ys = [b["y"] for b in out["bars"]]

    assert ys == sorted(ys) and len({b["y"] + b["h"] for b in out["bars"]}) == 4
    assert ys[0] >= out["head"]["h"]
    assert len({y2 - y1 for y1, y2 in pairwise(ys)}) == 1
    for b in out["bars"]:
        assert b["textTop"] >= b["y"] and b["textTop"] + b["textH"] <= b["y"] + b["h"]


@needs_node
def test_four_bars_with_a_note_stay_inside_the_safe_zone_and_are_centred():
    bars = [bar(f"bar number {i}", "about a figure", 20000 - 4000 * i, "gold", at=i) for i in range(4)]
    out = layout(bars, note="x" * 60)

    assert out["total"] <= SAFE_H
    assert out["stackY"] >= T.safe["top"]
    assert out["stackY"] + out["total"] <= T.canvas["height"] - T.safe["bottom"]
    assert out["stackY"] == js_round(T.safe["top"] + (SAFE_H - out["total"]) / 2)
    assert out["note"]["y"] >= out["bars"][-1]["y"] + out["bars"][-1]["h"]
    assert out["note"]["y"] + out["note"]["h"] == out["total"]


@needs_node
def test_a_title_or_a_note_wider_than_the_bars_is_refused():
    with pytest.raises(LogicError, match="title variable is too wide"):
        layout(title="W" * 40)
    with pytest.raises(LogicError, match="note variable is too wide"):
        layout(note="W" * 70)


@needs_node
def test_the_default_variables_lay_out_inside_the_safe_zone():
    assert layout_of(BLOCK, defaults(NAME))["total"] <= SAFE_H


# -- the time plan --------------------------------------------------------------------------------


@needs_node
def test_the_shortest_slot_is_the_last_bar_growing_its_text_arriving_the_reading_and_the_exit():
    got = minimum(
        [
            bar("The gold", "about a lot", 20000, at=1.0, seconds=1.2),
            bar("Less", "a little", 5000, at=0),
        ]
    )

    # 1.0 + 1.2 growing, 0.7 for the text to arrive, 1.2 + 0.3 a word of its 5 words, 0.5 out
    assert got == pytest.approx(1.0 + 1.2 + ENTER + reading(5) + EXIT)
    assert got == pytest.approx(6.1)


@needs_node
def test_the_shortest_slot_of_the_first_example_is_the_second_bar():
    bars = [AMERICA, LEDGER]
    ends = [b["at"] + b["seconds"] + ENTER + reading(len(f"{b['label']} {b['display']}".split())) for b in bars]

    assert page_minimum(BLOCK, defaults(NAME)) == pytest.approx(max(ends) + EXIT)
    assert minimum() == pytest.approx(8.5)
    assert ends[1] > ends[0]


@needs_node
def test_a_bar_that_is_not_the_last_may_decide_the_slot_and_bars_may_grow_together():
    slow = bar("one two three four", "five six seven eight", 20000, at=0, seconds=6)
    quick = bar("a", "b", 10000, at=0, seconds=0.3)

    assert minimum([slow, quick]) == pytest.approx(6 + ENTER + reading(8) + EXIT)
    assert minimum([quick, slow]) == minimum([slow, quick])


@needs_node
def test_the_title_and_the_note_are_read_too_so_a_long_one_can_decide_the_slot():
    quick = [
        bar("a", "b", 20000, at=0, seconds=0.3),
        bar("c", "d", 10000, at=0, seconds=0.3),
    ]
    note = "one two three four five six seven eight nine ten eleven twelve"

    assert minimum(quick, note=note) == pytest.approx(ENTER + reading(12) + EXIT)
    assert minimum(quick) == pytest.approx(0.3 + ENTER + reading(2) + EXIT)


@needs_node
def test_a_bar_must_take_at_least_the_micro_duration_to_grow():
    with pytest.raises(LogicError, match=r"bars\[0\]\.seconds is 0.2 s; a bar needs at least 0.3 s"):
        minimum([bar(seconds=0.2), bar()])


@needs_node
def test_a_layout_that_cannot_be_drawn_is_refused_by_minslot_too_so_the_preflight_sees_it():
    with pytest.raises(LogicError, match="fits neither inside its bar nor beside it"):
        minimum([bar(), bar("W" * 34, "W" * 24, 10000)])


@needs_node
def test_the_declared_floor_is_the_shortest_slot_any_legal_variables_need():
    smallest = page_minimum(
        BLOCK,
        variables(
            [
                bar("a", "b", 20000, at=0, seconds=D["micro"]),
                bar("c", "d", 10000, at=0, seconds=D["micro"]),
            ],
            title="a",
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


@pytest.mark.parametrize(
    ("bars", "scale"),
    [([AMERICA, LEDGER], 20000), ([SWISS, MARBURG], 400)],
    ids=["gold-ledger", "swiss-marburg"],
)
def test_both_examples_build_a_calm_timeline_and_draw_only_the_texts_of_their_variables(bars, scale):
    v = variables(bars, scale, note="Bundesbank figures")
    run = run_block(NAME, v | {"durationSeconds": 12})

    check_calm(run, NAME, 12)
    expected = {
        v["title"],
        v["note"],
        *(b["label"] for b in bars),
        *(b["display"] for b in bars),
    }
    assert sorted(texts(run)) == sorted(expected)


def test_text_is_set_trimmed_through_textcontent_and_never_as_markup():
    run = run_block(NAME, variables(title=" <b>Gold</b> ", note="A & B") | {"durationSeconds": 12})

    assert run.error is None
    assert "<b>Gold</b>" in texts(run) and "A & B" in texts(run)


def test_each_bar_grows_at_its_own_time_and_its_text_arrives_when_it_has_grown():
    run = run_block(NAME, variables() | {"durationSeconds": 12})
    wipes = tweens_on(run, "cb-bar")
    arrivals = tweens_on(run, "cb-text")

    assert all(t.frm == {"clipPath": "inset(0% 100% 0% 0%)"} for t in wipes)
    assert [(t.start, t.duration) for t in wipes] == [(1.0, 1.4), (2.8, 1.2)]
    assert all(t.ease == T.ease["enter"] for t in wipes)
    assert [t.start for t in arrivals] == pytest.approx([1.0 + 1.4, 2.8 + 1.2])
    assert all(t.duration == ENTER for t in arrivals)


def test_the_bars_are_sized_by_inline_geometry_on_one_scale_not_by_tweened_layout():
    run = run_block(NAME, variables() | {"durationSeconds": 12})
    bars = with_class(run, "cb-bar")

    assert [b["style"]["width"] for b in bars] == [
        f"{SAFE_W}px",
        f"{js_round(791 / 20000 * SAFE_W)}px",
    ]
    assert all(set(t.frm) <= {"opacity", "y", "clipPath"} for t in run.tweens)


def test_text_inside_a_long_bar_and_beside_a_short_one_get_their_class():
    run = run_block(NAME, variables() | {"durationSeconds": 12})

    assert [t["className"] for t in with_class(run, "cb-text")] == [
        "cb-text cb-inside cb-tone-paper",
        "cb-text cb-tone-gold",
    ]


@pytest.mark.parametrize("tone", ["gold", "paper", "ember", "mute"])
def test_every_tone_is_a_class_the_page_puts_on_the_bar_and_its_text(tone):
    run = run_block(
        NAME,
        variables([bar(tone=tone, value=20000), bar(value=10000)]) | {"durationSeconds": 12},
    )

    assert run.error is None
    assert with_class(run, "cb-bar")[0]["className"] == f"cb-bar cb-tone-{tone}"
    assert with_class(run, "cb-text")[0]["className"].endswith(f"cb-tone-{tone}")


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"bars": "[{oops"}, "bars variable is not valid JSON"),
        ({"bars": json.dumps([bar(tone="red"), bar()])}, r"bars\[0\]\.tone"),
        ({"bars": json.dumps([bar(value=30000), bar()])}, r"over scaleMax"),
        ({"title": "x" * 41}, "title variable is 41 characters"),
        ({"scaleMax": -1}, "scaleMax variable must be greater than 0"),
        ({"durationSeconds": "soon"}, "shorter than"),
    ],
)
def test_a_wrong_variable_fails_the_render_naming_it_and_registers_no_timeline(override, message):
    run = run_block(NAME, variables() | {"durationSeconds": 12} | override)

    assert re.search(message, run.error), run.error
    assert run.registered == []


def test_a_bar_that_would_end_after_the_slot_throws():
    run = run_block(NAME, variables([bar(at=8, seconds=3), bar()]) | {"durationSeconds": 10})

    assert "shorter than" in run.error
