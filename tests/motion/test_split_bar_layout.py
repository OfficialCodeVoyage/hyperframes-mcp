"""The geometry of the ``split-bar`` block, run in Node: the parts on one scale, the labels above or below the bar, the
brackets, the lanes and the width of every text from the advance table of the label face."""

from __future__ import annotations

import json
import math
import random
import string
from itertools import pairwise

import pytest

from hyperframes_mcp.motion import tokens
from tests.motion.bars_helpers import SAFE_H, SAFE_W, geometry, js_round, layout_of
from tests.motion.block_harness import T, defaults
from tests.motion.node_logic import LogicError, call, needs_node, run
from tests.motion.split_bar_helpers import (
    BLOCK,
    NAME,
    layout,
    marker,
    part,
    variables,
)

POLE_W, TEXT_INSET, FLAG_GAP = 4, 18, 36


# -- the advance table ----------------------------------------------------------------------------


@needs_node
def test_the_advance_table_is_never_narrower_than_the_label_face():
    """The widest of Medium, SemiBold and Bold of IBM Plex Sans Condensed, for every printable ASCII character."""
    fonttools = pytest.importorskip("fontTools.ttLib")
    fonts = [
        fonttools.TTFont(tokens.FONTS_DIR / f"IBMPlexSansCondensed-{w}.ttf") for w in ("Medium", "SemiBold", "Bold")
    ]
    chars = string.printable.strip() + " \u2018\u2019\u201c\u201d\u2013\u2014\u2026"
    widest = max(
        f["hmtx"][name][0] / f["head"].unitsPerEm
        for f in fonts
        for code, name in f.getBestCmap().items()
        if 0xA0 <= code <= 0x17F
    )
    # one Node run: the table's em of each character, then of three the table does not list
    ems, unlisted = run(
        BLOCK,
        f"emit([{json.dumps(list(chars))}.map(textEm), textEm('\\u00e9\\u0141\\u0152')]);",
    )
    for ch, em in zip(chars, ems, strict=True):
        real = max(f["hmtx"][f.getBestCmap()[ord(ch)]][0] / f["head"].unitsPerEm for f in fonts)
        assert em >= real - 1e-9, ch
    # a character the table does not list counts as the widest glyph of Latin-1 and Latin Extended-A
    assert unlisted >= 3 * widest - 1e-9


@needs_node
def test_a_text_width_is_the_table_times_the_size_rounded_up():
    assert call(BLOCK, "textWidth", "", 52) == 0
    assert call(BLOCK, "textWidth", "i", 52) == math.ceil(0.262 * 52)
    assert call(BLOCK, "blockWidth", "ii", "iiiii", 52) == call(BLOCK, "textWidth", "iiiii", 52)


@needs_node
def test_text_widths_are_at_least_the_real_ones_for_the_labels_of_the_story():
    fonttools = pytest.importorskip("fontTools.ttLib")
    bold = fonttools.TTFont(tokens.FONTS_DIR / "IBMPlexSansCondensed-Bold.ttf")

    def real(text: str, size: int) -> float:
        cmap, hmtx = bold.getBestCmap(), bold["hmtx"]
        return sum(hmtx[cmap[ord(c)]][0] for c in text) / bold["head"].unitsPerEm * size

    for text in (
        "The Bundesbank's gold ledger",
        "about a quarter of the ledger",
        "Marburg pile, Bundesbank's figure",
        "found in Germany",
    ):
        assert call(BLOCK, "textWidth", text, 52) >= real(text, 52) - 1e-6, text


# -- the scale ------------------------------------------------------------------------------------


@needs_node
def test_the_bar_is_the_safe_width_and_the_parts_sit_on_one_scale_from_the_left():
    out = layout()
    a, b = out["parts"]

    assert out["width"] == SAFE_W
    assert a["x0"] == 0
    assert a["x1"] == js_round(236 / 791 * SAFE_W)
    assert b["x0"] == a["x1"]
    assert b["x1"] == js_round(739 / 791 * SAFE_W)
    # the 52 left over are the faint track, unlabelled, from the end of the last part to the end of the whole
    assert b["x1"] < SAFE_W


@needs_node
def test_parts_that_fill_the_whole_end_at_the_end_of_the_bar():
    out = layout([part(value=400), part(value=391, at=1)], [])

    assert out["parts"][-1]["x1"] == SAFE_W


@needs_node
def test_a_marker_sits_anywhere_on_the_scale_not_only_inside_a_part():
    inside = layout(markers=[marker(from_=0, to=210)])["markers"][0]
    across = layout(markers=[marker(from_=200, to=600, label="spans two")])["markers"][0]
    rest = layout(markers=[marker(from_=745, to=791, label="the rest")])["markers"][0]

    assert inside["x0"] == 0 and inside["x1"] == js_round(210 / 791 * SAFE_W)
    assert across["x0"] == js_round(200 / 791 * SAFE_W)
    assert across["x1"] == js_round(600 / 791 * SAFE_W)
    assert rest["x1"] == SAFE_W
    for m in (inside, across, rest):
        assert 0 <= m["left"] and m["right"] <= SAFE_W
        assert m["bracketW"] == m["x1"] - m["x0"]


@needs_node
def test_the_default_marker_sits_inside_the_part_it_is_about():
    """Parts are placed in list order from the left, so 'found in Germany' first puts the Marburg range inside it."""
    out = layout()

    assert out["markers"][0]["x0"] >= out["parts"][0]["x0"]
    assert out["markers"][0]["x1"] <= out["parts"][0]["x1"]


@needs_node
def test_a_marker_whose_text_would_run_past_the_bar_is_right_aligned_to_its_bracket():
    out = layout(markers=[marker(from_=700, to=791, label="the last stretch", display="about a tenth")])["markers"][0]

    assert out["anchor"] == "end"
    assert out["right"] == SAFE_W
    assert out["left"] >= 0


@needs_node
def test_two_markers_that_overlap_are_refused_and_two_that_do_not_are_fine():
    with pytest.raises(LogicError, match=r"markers\[1\] and markers\[0\] overlap"):
        layout(
            markers=[
                marker(from_=0, to=200, label="first"),
                marker(from_=150, to=300, label="second"),
            ]
        )
    out = layout(
        markers=[
            marker(from_=0, to=100, label="first", display="a"),
            marker(from_=600, to=791, label="second", display="b"),
        ]
    )
    assert len(out["markers"]) == 2


# -- the labels -----------------------------------------------------------------------------------


@needs_node
def test_labels_sit_above_the_bar_when_there_is_room_each_with_a_pole_on_its_part():
    out = layout(markers=[])
    flags = out["flags"]

    assert [f["row"] for f in flags] == ["up", "up"]
    assert [f["anchor"] for f in flags] == ["start", "start"]
    for f, p in zip(flags, out["parts"], strict=True):
        assert f["left"] == p["x0"] and f["poleLeft"] == p["x0"] and f["poleX"] == 0
        assert f["textLeft"] == POLE_W + TEXT_INSET
        assert f["top"] + f["height"] == out["bar"]["y"]
    assert flags[0]["right"] + FLAG_GAP <= flags[1]["left"]


@needs_node
def test_a_four_percent_part_still_shows_its_label_below_the_bar_right_aligned_with_a_leader():
    """60, 36 and 4 percent: the third label would run into the second one above, and past the bar's end."""
    out = layout(
        [
            part("first part", "about 60 percent", 474, "gold"),
            part("second part", "about 36 percent", 285, "ember", at=1),
            part("sliver", "about four percent", 32, "paper", at=2),
        ],
        [],
    )
    third, flag = out["parts"][2], out["flags"][2]

    assert 60 < third["x1"] - third["x0"] < 80, "a part of four percent is about 70 px wide"
    assert [f["row"] for f in out["flags"]] == ["up", "up", "down"]
    assert flag["anchor"] == "end"
    assert flag["right"] == SAFE_W
    assert flag["poleLeft"] == third["x1"] - POLE_W
    assert flag["top"] == out["bar"]["y"] + out["bar"]["h"]


@needs_node
def test_a_narrow_part_before_a_wide_one_pushes_the_wide_ones_label_along_its_edge():
    out = layout(
        [
            part("tiny", "about 4 percent", 32, "gold"),
            part("small", "about 5 percent", 40, "paper", at=1),
            part("big", "about 80 percent", 640, "ember", at=2),
        ],
        [],
    )
    big, flag = out["parts"][2], out["flags"][2]

    assert [f["row"] for f in out["flags"]] == ["up", "down", "up"]
    assert all(f["anchor"] == "start" for f in out["flags"])
    # the pole of the third stands on its own part, clear of the first label
    assert big["x0"] < flag["poleLeft"] <= big["x1"] - POLE_W
    assert out["flags"][0]["right"] + FLAG_GAP <= flag["left"]


@needs_node
def test_a_label_below_the_bar_keeps_clear_of_a_bracket():
    out = layout(
        [
            part("first", "about a half", 395, "gold"),
            part("second", "about 46 percent", 365, "ember", at=1),
            part("tail", "about four percent", 31, "paper", at=2),
        ],
        [marker(from_=0, to=250, label="the first stretch", display="about a third")],
    )
    taken = out["markers"][0]
    down = [f for f in out["flags"] if f["row"] == "down"]

    assert [f["row"] for f in out["flags"]] == ["up", "up", "down"]
    assert down[0]["left"] >= taken["right"] + FLAG_GAP


@needs_node
def test_a_bracket_that_takes_the_lane_below_leaves_a_narrow_part_without_a_place():
    with pytest.raises(LogicError, match=r'label of parts\[2\] \("tail"\) has no free place'):
        layout(
            [
                part("first", "about a half", 395, "gold"),
                part("second", "about 46 percent", 365, "ember", at=1),
                part("tail", "about four percent", 31, "paper", at=2),
            ],
            [marker(from_=500, to=791, label="the tail end", display="the end of it")],
        )


@needs_node
def test_a_label_with_no_free_place_throws_and_names_the_part():
    crowded = [part(f"label {i}", "about", 20 if i else 30, "gold", at=i) for i in range(4)]

    with pytest.raises(LogicError, match=r'label of parts\[2\] \("label 2"\) has no free place'):
        layout(crowded, [])


@needs_node
def test_labels_never_overlap_in_a_row_and_stay_in_the_bar_for_random_parts():
    rng = random.Random(7)
    cases = []
    for _ in range(120):
        count = rng.randint(1, 5)
        cuts = sorted(rng.sample(range(1, 100), count - 1))
        shares = [b - a for a, b in zip([0, *cuts], [*cuts, 100], strict=True)]
        shares = [max(s * rng.uniform(0.2, 1) if s > 2 else s, 1.0) for s in shares]
        parts = [
            part(
                rng.choice(
                    [
                        "gold",
                        "silver",
                        "the Bundesbank",
                        "went abroad",
                        "found in Germany",
                    ]
                ),
                rng.choice(["about a third", "about four percent", "nearly all of it"]),
                s,
                rng.choice(["gold", "paper", "ember", "mute"]),
                at=i,
            )
            for i, s in enumerate(shares)
        ]
        cases.append(variables(parts, [], whole=100))
    # one Node run for all of them: a layout, or the error the block threw
    results = run(
        BLOCK,
        f"const G = {json.dumps(geometry())};"
        f"emit({json.dumps(cases)}.map((V) => {{"
        " try { return { out: layoutFor(readContent(V), G) }; }"
        " catch (e) { return { error: e.message }; } }));",
    )
    placed = refused = 0
    for result in results:
        if "error" in result:
            assert "no free place" in result["error"], result["error"]
            refused += 1
            continue
        placed += 1
        out = result["out"]
        for row in ("up", "down"):
            row_flags = sorted((f for f in out["flags"] if f["row"] == row), key=lambda f: f["left"])
            for f in row_flags:
                assert 0 <= f["left"] and f["right"] <= SAFE_W
            for a, b in pairwise(row_flags):
                assert a["right"] + FLAG_GAP <= b["left"]
        for f, p in zip(out["flags"], out["parts"], strict=True):
            # the pole stands on its own part
            assert p["x0"] <= f["poleLeft"] <= max(p["x0"], p["x1"] - POLE_W)
    assert placed > 80, (placed, refused)


# -- the lanes and the safe zone ------------------------------------------------------------------


@needs_node
def test_the_header_label_and_value_must_share_one_line():
    with pytest.raises(LogicError, match="wholeLabel and wholeValue variables do not fit"):
        layout(wholeLabel="W" * 34, wholeValue="W" * 24)


@needs_node
def test_a_note_sits_below_everything_and_one_wider_than_the_bar_is_refused():
    out = layout(note="The ledger's own figures")

    assert out["note"]["y"] >= out["markers"][0]["top"] + out["markers"][0]["height"]
    assert out["note"]["y"] + out["note"]["h"] == out["total"]
    with pytest.raises(LogicError, match="note variable is too wide"):
        layout(note="W" * 70)


@needs_node
def test_the_tallest_graphic_the_variables_allow_stays_inside_the_safe_zone():
    parts = [
        part("first lot", "a fifth", 200, "gold", at=0),
        part("second lot", "a quarter", 200, "ember", at=1),
        part("third lot", "a quarter", 200, "paper", at=2),
        part("fourth lot", "a fifth", 191, "mute", at=3),
    ]
    markers = [
        marker(from_=0, to=100, label="the first range", display="about an eighth"),
        marker(from_=300, to=450, label="the middle range", display="about a fifth"),
        marker(from_=600, to=790, label="the last range", display="about a fifth"),
    ]
    out = layout(parts, markers, note="x" * 60)

    # flags above, brackets below and a note: the most the lanes hold at once
    assert {f["row"] for f in out["flags"]} == {"up"}
    assert len(out["markers"]) == 3 and out["note"]
    assert out["total"] <= SAFE_H
    assert out["stackY"] >= T.safe["top"]
    assert out["stackY"] + out["total"] <= T.canvas["height"] - T.safe["bottom"]
    assert out["stackX"] == T.safe["left"] and out["width"] == SAFE_W


@needs_node
def test_the_graphic_is_centred_in_the_safe_zone_with_everything_inside_its_box():
    out = layout()

    assert out["stackY"] == js_round(T.safe["top"] + (SAFE_H - out["total"]) / 2)
    for item in (*out["flags"], *out["markers"]):
        assert 0 <= item["top"] and item["top"] + item["height"] <= out["total"]
    assert out["bar"]["y"] >= out["head"]["h"]


@needs_node
def test_the_lanes_are_only_as_tall_as_what_uses_them():
    full = layout()
    plain = layout(markers=[])
    flat = layout([part("a", "b", 400, "gold"), part("c", "d", 391, "ember", at=1)], [])

    assert plain["total"] < full["total"]
    assert plain["total"] == flat["total"]
    # with every label above the bar and no bracket, nothing hangs below it
    assert plain["total"] == plain["bar"]["y"] + plain["bar"]["h"]


@needs_node
def test_the_default_variables_lay_out_inside_the_safe_zone():
    assert layout_of(BLOCK, defaults(NAME))["total"] <= SAFE_H
