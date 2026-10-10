"""The ``ledger`` block: a paper sheet that fills row by row, then a total and a big figure.

The static checks keep the promises of the contract. The logic checks run the block's own functions in Node
(``tests/motion/node_logic.py``): the shape of the variables, the fit of the rows in the safe zone, the time plan
(rows at the narration's cues or evenly spaced, one wipe at a time, the big figure last, a hold to read it).
"""

from __future__ import annotations

import json
import re
import string
from functools import partial
from itertools import pairwise

import pytest

from hyperframes_mcp.motion import registry, tokens
from tests.motion import block_helpers, node_logic
from tests.motion.block_helpers import code_of
from tests.motion.node_logic import LogicError, call, needs_node

BLOCK = tokens.MOTION_DIR / "blocks" / "ledger.html"
TOK = tokens.default_tokens()
html = partial(block_helpers.html, BLOCK)
declared = partial(block_helpers.declared, BLOCK)
defaults = partial(block_helpers.defaults, BLOCK)


def row(label: str = "Gold bars", value: str = "8,198", unit: str = "bars") -> dict:
    return {"label": label, "value": value, "unit": unit}


def content(rows: list[dict] | None = None, **over) -> dict:
    base = {
        "title": "Vault inventory",
        "rows": rows if rows is not None else [row()],
        "total": {"label": "Items counted", "value": "10,384"},
        "big": None,
        "rowTimes": None,
    }
    return base | over


def layout_input(c: dict) -> dict:
    return {
        "content": c,
        "ledger": TOK.sizes["ledger"],
        "subtitle": TOK.sizes["subtitle"],
        "safeW": TOK.canvas["width"] - TOK.safe["left"] - TOK.safe["right"],
        "safeH": TOK.canvas["height"] - TOK.safe["top"] - TOK.safe["bottom"],
    }


def plan_input(**over) -> dict:
    d = TOK.duration
    base = {
        "duration": 12,
        "rows": 4,
        "rowTimes": None,
        "hasBig": True,
        "words": 9,
        "enter": d["enter"],
        "exit": d["exit"],
        "micro": d["micro"],
        "cinMin": d["cinematic_min"],
        "readMin": d["read_min"],
        "readPerWord": d["read_per_word"],
    }
    return base | over


def reading(words: int) -> float:
    return TOK.duration["read_min"] + TOK.duration["read_per_word"] * words


# -- static ---------------------------------------------------------------------------------------


def test_it_declares_its_variables_durationseconds_first_and_is_our_own():
    """That the registry repeats the declaration, lints clean and has the root duration is checked for every item."""
    assert [v["id"] for v in declared()] == [
        "durationSeconds",
        "title",
        "rows",
        "total",
        "bigFigure",
        "rowTimes",
    ]
    assert registry.get("ledger").source is None


def test_the_defaults_are_a_good_sheet():
    d = defaults()
    rows = json.loads(d["rows"])

    assert 1 <= len(rows) <= 8
    assert all({"label", "value", "unit"} <= set(r) for r in rows)
    assert set(json.loads(d["total"])) == {"label", "value"}
    assert set(json.loads(d["bigFigure"])) == {"value", "caption"}
    assert d["rowTimes"] == ""
    assert len(d["title"]) <= next(v["maxLength"] for v in declared() if v["id"] == "title")


def test_it_registers_one_paused_timeline_and_tweens_only_with_fromto():
    text = code_of(html())

    assert 'data-composition-id="ledger"' in text
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert 'window.__timelines["ledger"] = tl;' in text
    assert ".fromTo(" in text
    # the linter forbids tl.from and bare gsap tweens; the rest of the timeline's methods are for this block to keep out
    assert not re.search(r"\btl\.(?:to|call|add|set)\(", text)


def test_the_rows_are_written_by_a_left_to_right_clip_path_never_by_timers_or_steps():
    text = code_of(html())

    assert 'clipPath: "inset(0% 100% 0% 0%)"' in text
    assert 'clipPath: "inset(0% 0% 0% 0%)"' in text
    assert re.search(r"duration: plan\.rows\[i\]\.dur, ease: token\(\"--ease-move\"\)", text)
    assert not re.search(r"\bstagger\s*:", text)
    assert "steps(" not in text
    assert 'split("")' not in text and "split('')" not in text, "no per-character spans"


def test_the_total_draws_its_rule_with_scalex_and_the_big_figure_lands_with_the_emphasis_ease():
    text = code_of(html())

    assert re.search(r"totalRuleEl, \{ scaleX: 0 \}, \{ scaleX: 1,", text)
    assert "transform-origin: 0 50%;" in text
    assert re.search(
        r"bigValueEl,.*?number\(\"--dur-cinematic-min\"\), ease: token\(\"--ease-emphasis\"\) \},\s*plan\.bigAt",
        text,
        re.DOTALL,
    )
    assert "color: var(--color-oxblood);" in text
    assert "background: var(--color-oxblood);" in text


def test_it_is_a_paper_sheet_in_ink_with_ruled_lines_and_right_aligned_tabular_figures():
    text = html()

    assert "background: var(--color-paper);" in text
    assert "color: var(--color-ink);" in text
    assert "font-family: var(--font-mono);" in text
    assert "font-family: var(--font-display);" in text
    assert "font-family: var(--font-label);" in text
    assert "font-variant-numeric: tabular-nums;" in text
    assert re.search(r"\.row \.value \{\s*text-align: right;", text)
    assert "background-size: 100% var(--row-h);" in text
    assert "font-size: var(--size-ledger);" in text


def test_the_big_figure_and_its_caption_stay_readable_over_a_white_frame():
    """Gold and paper on the backing at its CSS opacity over pure white, the worst case, are 4.5:1 or better."""
    assert block_helpers.scrim_contrast(html(), "gold") >= TOK.min_ratio
    assert block_helpers.scrim_contrast(html(), "paper") >= TOK.min_ratio
    assert ("ink", "paper") in TOK.text_pairs
    assert ("oxblood", "paper") in TOK.text_pairs
    assert ("gold", "night") in TOK.text_pairs


def test_everything_the_viewer_reads_is_inside_the_safe_zone():
    text = html()

    for side in ("left", "right", "top", "bottom"):
        assert f"{side}: var(--safe-{side});" in text


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    text = code_of(html())

    assert "getComputedStyle(rootEl)" in text
    assert "if (raw)" in text
    assert "getVariables" in text
    assert 'document.querySelector("[data-composition-variables]")' not in text
    assert "getElementById" not in text
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text
    assert text.count("textContent") >= 6


def test_the_logic_markers_exist_once_and_hold_no_dom():
    text = html()

    assert text.count("// logic:begin") == 1
    assert text.count("// logic:end") == 1
    assert node_logic.logic_of(BLOCK).count("document") == 0


# -- logic, run in Node --------------------------------------------------------------------------


@needs_node
def test_a_json_variable_is_parsed_passed_through_or_none():
    assert call(BLOCK, "parseVar", '[{"a": 1}]', "rows") == [{"a": 1}]
    assert call(BLOCK, "parseVar", [{"a": 1}], "rows") == [{"a": 1}]
    assert call(BLOCK, "parseVar", "", "bigFigure") is None
    assert call(BLOCK, "parseVar", "null", "bigFigure") is None
    with pytest.raises(LogicError, match="rows variable is not valid JSON"):
        call(BLOCK, "parseVar", "[{oops", "rows")


@needs_node
def test_the_variables_are_checked_and_numbers_become_text():
    raw = {
        "title": "Reserve",
        "rows": [
            {"label": "Bars", "value": 8198},
            {"label": "Crates", "value": "55", "unit": None},
        ],
        "total": {"label": "Items", "value": 8253},
        "bigFigure": None,
        "rowTimes": None,
    }
    out = call(BLOCK, "normalize", raw)

    assert out["rows"] == [
        {"label": "Bars", "value": "8198", "unit": ""},
        {"label": "Crates", "value": "55", "unit": ""},
    ]
    assert out["total"] == {"label": "Items", "value": "8253"}
    assert out["big"] is None and out["rowTimes"] is None


@needs_node
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rows": []}, "at least one row"),
        ({"rows": "nope"}, "at least one row"),
        ({"rows": [{"value": "1"}]}, "row 1 label is missing"),
        ({"rows": [{"label": "A"}]}, "row 1 value is missing"),
        ({"title": "  "}, "the title is missing"),
        ({"total": {"label": "T"}}, "the total value is missing"),
        ({"bigFigure": {"caption": "c"}}, "big figure value is missing"),
        ({"rowTimes": "1,2"}, "rowTimes must be a list"),
    ],
)
def test_a_missing_or_wrong_field_throws_instead_of_drawing_a_wrong_sheet(change, message):
    raw = {
        "title": "T",
        "rows": [{"label": "A", "value": "1", "unit": "x"}],
        "total": {"label": "Total", "value": "1"},
        "bigFigure": None,
        "rowTimes": None,
    } | change

    with pytest.raises(LogicError, match=message):
        call(BLOCK, "normalize", raw)


@needs_node
def test_eight_rows_fit_the_safe_zone_with_room_to_spare_and_nine_do_not():
    t = TOK
    rows = [row(f"Row number {i}", "1,234,567", "units") for i in range(8)]
    fit = call(BLOCK, "layoutFor", layout_input(content(rows)))
    safe_h = t.canvas["height"] - t.safe["top"] - t.safe["bottom"]

    assert fit["maxRows"] == 8
    assert fit["sheetH"] <= safe_h
    # the rule of the layout, written out: padding, heading, rule gap, rows, total gap, total rule and the total row
    ledger, subtitle = t.sizes["ledger"], t.sizes["subtitle"]
    row_h = round(1.6 * ledger)
    expected = (
        2 * round(0.8 * ledger)
        + round(1.15 * subtitle)
        + round(0.6 * ledger)
        + 8 * row_h
        + round(0.3 * ledger)
        + 4
        + row_h
    )
    assert fit["sheetH"] == expected
    with pytest.raises(LogicError, match="9 rows do not fit the sheet: at most 8"):
        call(BLOCK, "layoutFor", layout_input(content([row()] * 9)))


@needs_node
def test_the_sheet_and_the_big_column_share_the_safe_width():
    fit = call(BLOCK, "layoutFor", layout_input(content()))
    safe_w = TOK.canvas["width"] - TOK.safe["left"] - TOK.safe["right"]

    assert fit["sheetW"] + fit["gap"] + fit["columnW"] == safe_w
    assert fit["gap"] > 0 and fit["columnW"] > 0


@needs_node
def test_a_label_title_or_total_longer_than_its_column_is_an_error_that_names_it():
    fit = call(BLOCK, "layoutFor", layout_input(content()))
    long_label = "x" * (fit["labelMax"] + 1)

    ok = call(BLOCK, "layoutFor", layout_input(content([row("x" * fit["labelMax"])])))
    assert ok["labelMax"] == fit["labelMax"]
    with pytest.raises(LogicError, match="row 2 label"):
        call(BLOCK, "layoutFor", layout_input(content([row(), row(long_label)])))
    with pytest.raises(LogicError, match="title is"):
        call(BLOCK, "layoutFor", layout_input(content(title="T" * 60)))
    with pytest.raises(LogicError, match="total line"):
        call(
            BLOCK,
            "layoutFor",
            layout_input(content(total={"label": "L" * 40, "value": "9" * 12})),
        )


@needs_node
def test_the_declared_title_limit_fits_and_the_default_content_passes():
    """The title limit is an average-width estimate (letters vary), not a guarantee for every all-capitals title."""
    d = defaults()
    c = content(
        json.loads(d["rows"]),
        title="W" * next(v["maxLength"] for v in declared() if v["id"] == "title"),
        total=json.loads(d["total"]),
    )

    assert call(BLOCK, "layoutFor", layout_input(c))["maxRows"] == 8


@needs_node
def test_the_character_width_of_the_ledger_face_is_the_one_the_layout_assumes():
    fonttools = pytest.importorskip("fontTools.ttLib")
    char_w = call(BLOCK, "layoutFor", layout_input(content()))["charW"]

    for name in ("CourierPrime-Regular.ttf", "CourierPrime-Bold.ttf"):
        font = fonttools.TTFont(tokens.FONTS_DIR / name)
        em = font["hmtx"][font.getBestCmap()[ord("0")]][0] / font["head"].unitsPerEm
        assert char_w == pytest.approx(em * TOK.sizes["ledger"], rel=1e-3), name


@needs_node
def test_the_big_figure_is_as_large_as_its_column_allows_and_never_under_the_title_size():
    t = TOK
    column = call(BLOCK, "layoutFor", layout_input(content()))["columnW"]
    base = {
        "width": column,
        "max": round(t.sizes["title"] * 2.2),
        "min": t.sizes["title"],
    }
    default = json.loads(defaults()["bigFigure"])["value"]

    assert call(BLOCK, "bigSize", {**base, "text": "98"}) == base["max"]
    assert t.sizes["title"] <= call(BLOCK, "bigSize", {**base, "text": default}) <= base["max"]
    with pytest.raises(LogicError, match="does not fit its column"):
        call(BLOCK, "bigSize", {**base, "text": "2.76 billion marks"})


@needs_node
def test_the_width_table_is_never_narrower_than_any_printable_ascii_character():
    """The table itself, not five sample strings: M, W, m and @ are wider than the default for other letters."""
    advance = node_logic.newsreader_advances(500, 72)

    for ch in string.printable.strip():
        assert call(BLOCK, "bigEm", ch) >= advance[ch], ch


@needs_node
def test_the_estimate_of_the_big_figure_is_never_narrower_than_the_font():
    """Measured on Newsreader at weight 500 and optical size 72, which is what the browser draws at these sizes.

    The first version used the advances of the file's default instance and drew "98.6%" 50 px outside the safe zone.
    """
    advance = node_logic.newsreader_advances(500, 72)
    column = call(BLOCK, "layoutFor", layout_input(content()))["columnW"]
    base = {
        "width": column,
        "max": round(TOK.sizes["title"] * 2.2),
        "min": TOK.sizes["title"],
    }
    for text in ("98.6%", "$4.2B", "1,917", "x3", "100%"):
        size = call(BLOCK, "bigSize", {**base, "text": text})
        real = sum(advance[c] for c in text) * size
        assert real <= column, text


@needs_node
def test_rows_are_spaced_evenly_after_the_arrival_one_wipe_at_a_time():
    d = TOK.duration
    out = call(BLOCK, "schedule", plan_input())
    rows = out["rows"]

    assert rows[0]["at"] == pytest.approx(d["enter"]), "the first row starts when the sheet is in"
    gaps = [b["at"] - a["at"] for a, b in pairwise(rows)]
    assert all(g == pytest.approx(gaps[0]) for g in gaps)
    assert d["micro"] <= gaps[0] <= d["cinematic_min"]
    for a, b in pairwise(rows):
        assert a["at"] + a["dur"] <= b["at"] + 1e-9, "a wipe ends before the next starts"
    assert all(d["micro"] <= r["dur"] <= d["enter"] for r in rows)


@needs_node
def test_the_total_follows_the_rows_and_the_big_figure_lands_last_then_holds_to_be_read():
    d = TOK.duration
    out = call(BLOCK, "schedule", plan_input())
    last = out["rows"][-1]

    assert out["ruleAt"] >= last["at"] + last["dur"]
    assert out["totalAt"] > out["ruleAt"]
    assert out["bigAt"] >= out["totalAt"] + d["enter"]
    assert out["captionAt"] > out["bigAt"]
    assert out["holdFrom"] == pytest.approx(out["bigAt"] + d["cinematic_min"])
    assert out["captionAt"] - out["bigAt"] + d["enter"] <= d["cinematic_min"]
    assert out["exitAt"] - out["holdFrom"] >= reading(9) - 1e-9


@needs_node
def test_without_a_big_figure_the_total_is_last():
    d = TOK.duration
    out = call(BLOCK, "schedule", plan_input(hasBig=False, words=3))

    assert out["bigAt"] is None and out["captionAt"] is None
    assert out["holdFrom"] == pytest.approx(out["totalAt"] + d["enter"])
    assert out["exitAt"] - out["holdFrom"] >= reading(3) - 1e-9


@needs_node
def test_a_long_slot_spreads_the_rows_no_wider_than_the_cinematic_minimum():
    out = call(BLOCK, "schedule", plan_input(duration=40))
    gap = out["rows"][1]["at"] - out["rows"][0]["at"]

    assert gap == pytest.approx(TOK.duration["cinematic_min"])
    assert out["exitAt"] - out["holdFrom"] > reading(9)


@needs_node
def test_rows_come_at_the_cues_the_narration_gives():
    cues = [1.0, 3.5, 4.2, 9.0]
    out = call(BLOCK, "schedule", plan_input(duration=20, rowTimes=cues))

    assert [r["at"] for r in out["rows"]] == cues
    # a wipe is as long as the next cue allows, never longer than the enter duration
    assert [round(r["dur"], 3) for r in out["rows"]] == [0.7, 0.7, 0.7, 0.7]
    close = call(BLOCK, "schedule", plan_input(duration=20, rowTimes=[1.0, 1.4, 4.0, 5.0]))
    assert close["rows"][0]["dur"] == pytest.approx(0.4)
    assert close["ruleAt"] >= 5.0 + TOK.duration["enter"]


@needs_node
@pytest.mark.parametrize(
    ("cues", "message"),
    [
        ([1.0, 2.0], "lists 2 times for 4 rows"),
        ([1.0, 2.0, "x", 4.0], "rowTimes\\[2\\] is not a number"),
        (
            [1.0, 1.1, 2.0, 3.0],
            "rowTimes\\[1\\] is less than 0.3 s after the row before",
        ),
        ([0.2, 2.0, 3.0, 4.0], "cannot appear before 0.35 s"),
        ([1.0, 2.0, 3.0, 11.0], "durationSeconds"),
    ],
)
def test_cues_that_cannot_be_drawn_throw_with_the_reason(cues, message):
    with pytest.raises(LogicError, match=message):
        call(BLOCK, "schedule", plan_input(rowTimes=cues))


@needs_node
def test_a_slot_too_short_for_the_rows_the_total_the_figure_and_the_reading_throws():
    with pytest.raises(LogicError, match="durationSeconds 8 is too short"):
        call(BLOCK, "schedule", plan_input(duration=8))
    # one row and no figure is the smallest sheet; the declared minimum must run it
    minimum = next(v["min"] for v in declared() if v["id"] == "durationSeconds")
    out = call(BLOCK, "schedule", plan_input(duration=minimum, rows=1, hasBig=False, words=2))
    assert out["exitAt"] - out["holdFrom"] >= reading(2) - 1e-9


@needs_node
def test_the_default_slot_runs_the_default_sheet():
    d = defaults()
    big = json.loads(d["bigFigure"])
    words = len((big["value"] + " " + big["caption"]).split())
    out = call(
        BLOCK,
        "schedule",
        plan_input(duration=d["durationSeconds"], rows=len(json.loads(d["rows"])), words=words),
    )

    assert out["exitAt"] - out["holdFrom"] >= reading(words) - 1e-9


@needs_node
def test_words_are_counted_for_the_reading_time():
    assert call(BLOCK, "wordCount", "  of the gold, per  the final ") == 6
