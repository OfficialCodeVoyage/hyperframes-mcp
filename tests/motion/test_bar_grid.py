"""The ``bar-grid`` block: a pictogram of one bar glyph per bar, filled in segments as the narrator speaks.

This file holds the static checks of the contract, the refusals and the slot minimum. The layout and the font tables are
in ``test_bar_grid_layout.py``, the drawing (which cell is lit when, the outline, the seeks) in ``test_bar_grid_draw.py``;
all run the block's own functions in Node (``tests/motion/node_logic.py``). The frames of a real render are looked at
separately (``tests/motion/test_render_integration.py``, opt-in).
"""

from __future__ import annotations

import json
import re

import pytest

from hyperframes_mcp.motion import preflight, registry
from hyperframes_mcp.motion.compose import plan
from hyperframes_mcp.motion.shotlist import parse_shotlist
from tests.conftest import REPO
from tests.motion import block_helpers
from tests.motion.bar_grid_helpers import (
    BLOCK,
    COLD_OPEN,
    FOR_SCALE,
    T,
    check_slot,
    declared,
    defaults,
    error_of,
    hand_minimum,
    html,
    js,
    seg,
    slot,
    variables,
)
from tests.motion.block_harness import (
    check_max_lengths,
    check_mounted,
    check_static,
)
from tests.motion.block_helpers import code_of
from tests.motion.block_runner import css_properties
from tests.motion.node_logic import needs_node, run

REGISTRY = registry.default_registry()

# -- static ---------------------------------------------------------------------------------------


def test_it_declares_its_variables_durationseconds_first_and_is_our_own():
    """That the registry repeats the declaration, lints clean and has the root duration is checked for every item."""
    assert [v["id"] for v in declared()] == [
        "durationSeconds",
        "title",
        "total",
        "segments",
        "unit",
        "note",
    ]
    assert registry.get("bar-grid").source is None
    assert registry.get("bar-grid").type == "hyperframes:block"


def test_the_limits_the_variables_declare_are_the_ones_the_script_enforces():
    text = code_of(html())

    check_max_lengths("bar-grid", {"title": "MAX_TITLE", "unit": "MAX_UNIT", "note": "MAX_NOTE"})
    total = next(v for v in declared() if v["id"] == "total")
    assert total["min"] == 1
    assert total["max"] == int(re.search(r"MAX_TOTAL = (\d+)", text).group(1)) == 20000
    assert "at most 5" in next(v for v in declared() if v["id"] == "segments")["label"]
    assert int(re.search(r"MAX_SEGMENTS = (\d+)", text).group(1)) == 5
    assert int(re.search(r"MAX_LABEL = (\d+)", text).group(1)) == 38
    assert "label at most 38 characters" in next(v for v in declared() if v["id"] == "segments")["label"]


def test_the_default_variables_are_the_cold_open_and_make_a_frame_on_their_own():
    d = defaults()

    assert d["total"] == 8198 and d["unit"] == "bars" and d["note"] == ""
    assert d["title"] == "Marburg: gold bars in the first partial count"
    assert json.loads(d["segments"]) == [seg(count=8198, at=0.4, seconds=3.0)]
    # the static minimum of the slot is under the default (the block's own minimum is checked with the preflight below)
    assert next(v["min"] for v in declared() if v["id"] == "durationSeconds") <= d["durationSeconds"]


def test_it_registers_one_paused_timeline_and_tweens_only_with_fromto():
    """One paused timeline under the id, fromTo only, no literal colour, no markup, no shadow or filter."""
    check_static("bar-grid")
    assert 'data-composition-id="bar-grid"' in code_of(html())
    assert not re.search(r"\btl\.(?:call|add)\(", code_of(html()))


def test_the_grid_is_drawn_by_a_modifier_on_one_linear_ticker_never_by_a_callback():
    """A modifier runs on every render of the tween, a callback is skipped by a quiet seek (see the counter's tests)."""
    text = code_of(html())

    assert "onUpdate" not in re.sub(r"//[^\n]*", "", text)
    ticker = re.search(
        r"function addTicker\(tl, duration, show\) \{.*?tl\.fromTo\(\s*clock,\s*\{ t: 0 \},\s*\{(.*?)\n\s*\},\s*0,\s*\);",
        text,
        re.DOTALL,
    )
    assert ticker, "the ticker tween is not the one the tests know"
    body = ticker.group(1)
    assert "t: duration," in body and "duration: duration," in body
    assert 'ease: "none"' in body and "modifiers:" in body and "show(value)" in body
    # linear motion is allowed only where the element carries the marker the linter looks for
    assert text.count('ease: "none"') == 1
    assert "data-linear-ok" in text
    # the page's render is the draw and the counts, both from the time alone, and the ticker shows them
    assert "addTicker(tl, duration, render);" in text
    render = re.search(r"function render\(t\) \{(.*?)\n        \}", text, re.DOTALL)
    assert render
    assert "drawFrame(litCtx, model, sprites, t)" in render.group(1)
    assert "startedCells(seg, t)" in render.group(1)


def test_the_page_arrives_together_and_a_legend_row_arrives_when_its_segment_starts():
    text = code_of(html())

    for el in ("scrimEl", "titleEl", "gridEl"):
        assert re.search(rf"fromTo\({el},.*?,\s*0\)", text, re.DOTALL), el
    assert re.search(r"fromTo\(chipEls\[k\],.*?, seg\.at\)", text, re.DOTALL)
    # the slot ends with the fade out of the whole stage
    assert re.search(r"stageEl,\s*\{ opacity: 1 \},.*?duration - timing\.exit", text, re.DOTALL)


def test_every_colour_is_a_token_and_the_tones_name_real_ones():
    text = code_of(html())
    props = css_properties()

    tones = dict(
        re.findall(
            r'(\w+): "(--color-[\w-]+)"',
            re.search(r"var TONE_TOKEN = \{(.*?)\};", text, re.DOTALL).group(1),
        )
    )
    assert set(tones) == {"gold", "paper", "ember", "mute"}
    assert tones["ember"] == "--color-loss"
    assert all(token in props for token in tones.values())
    # the canvas colours are read from the page's tokens and never written as literals
    assert 'token("--color-paper")' in text and "token(TONE_TOKEN[tone])" in text


def test_all_text_is_at_the_token_sizes_and_never_under_the_minimum():
    text = html()

    assert "font-size: var(--size-subtitle);" in text
    assert text.count("font-size: var(--size-min-text);") == 1  # unit, label and note share one rule
    for name in ("unit", "label", "note"):
        assert f"#bar-grid-root .{name}" in text
    assert "font-size: var(--count-size);" in text
    assert not re.search(r"font-size:\s*\d+px", text)


def test_everything_the_viewer_reads_is_inside_the_safe_zone():
    text = html()

    assert "left: var(--safe-left);" in text
    assert "right: var(--safe-right);" in text
    assert "top: var(--stack-top);" in text
    assert "height: var(--stack-h);" in text


def test_the_text_stays_readable_over_a_white_frame():
    """Each text colour on the scrim at its strongest CSS opacity over pure white, the worst case, is 4.5:1 or better."""
    for colour in ("gold", "loss", "paper", "paper_aged"):
        assert block_helpers.scrim_contrast(html(), colour) >= T.min_ratio, colour


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    check_mounted("bar-grid")
    text = code_of(html())

    assert 'document.querySelector("[data-composition-variables]")' not in text
    assert "getElementById" not in text


def test_a_missing_variable_throws_instead_of_drawing_a_wrong_frame():
    text = code_of(html())

    for name in ("title", "unit", "note"):
        assert f'needText(V, "{name}"' in text, name
    assert "the total variable is missing" in text
    assert "the segments variable is missing" in text
    assert "innerHTML" not in text


def test_there_is_no_clock_no_randomness_and_no_css_motion():
    text = code_of(html())

    assert not re.search(
        r"Math\.random|Date\.now|performance\.now|requestAnimationFrame|setTimeout|setInterval",
        text,
    )
    assert not re.search(r"\btransition\b|@keyframes|\banimation\s*:", text)


def test_the_logic_markers_exist_once_and_hold_no_dom():
    text = html()

    assert text.count("// logic:begin") == 1
    assert text.count("// logic:end") == 1
    logic = text.split("// logic:begin")[1].split("// logic:end")[0]
    assert "function minSlot(V, tok)" in logic
    assert "document" not in logic and "getComputedStyle" not in logic
    assert "window" not in logic


# -- the refusals, run in Node ---------------------------------------------------------------------


@needs_node
def test_the_variables_of_both_shots_and_the_defaults_are_accepted():
    assert error_of(defaults()) is None
    assert error_of(COLD_OPEN) is None
    assert error_of(FOR_SCALE) is None


def segs(*segments: dict) -> str:
    return json.dumps(list(segments))


@needs_node
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"segments": "[{not json"}, r"segments variable is not valid JSON"),
        ({"segments": "{}"}, r"segments variable must be a list"),
        ({"segments": "[]"}, r"segments variable must be a list with at least one"),
        ({"segments": "null"}, r"segments variable must be a list"),
        (
            {"segments": segs(*[seg(count=1)] * 6)},
            r"segments variable lists 6 segments; at most 5",
        ),
        ({"segments": segs(1)}, r"segments\[0\] must be an object"),
        (
            {"segments": segs(seg(count=0))},
            r"segments\[0\]\.count must be a whole number",
        ),
        (
            {"segments": segs(seg(count=1.5))},
            r"segments\[0\]\.count must be a whole number",
        ),
        (
            {"segments": segs(seg(count="3"))},
            r"segments\[0\]\.count must be a whole number",
        ),
        (
            {"segments": segs(seg(label=""))},
            r"segments\[0\]\.label is missing or empty",
        ),
        (
            {"segments": segs(seg(label="   "))},
            r"segments\[0\]\.label is missing or empty",
        ),
        (
            {"segments": segs(seg(label="x" * 39))},
            r"segments\[0\]\.label is 39 characters; at most 38",
        ),
        (
            {"segments": segs(seg(tone="teal"))},
            r'segments\[0\]\.tone "teal" is not one of gold, paper, ember, mute',
        ),
        (
            {"segments": segs(seg(at=-0.1))},
            r"segments\[0\]\.at must be a number of seconds, 0 or more",
        ),
        ({"segments": segs(seg(at="1"))}, r"segments\[0\]\.at must be a number"),
        (
            {"segments": segs(seg(seconds=0))},
            r"segments\[0\]\.seconds must be a number of seconds, more than 0",
        ),
        ({"segments": segs(seg(seconds=-2))}, r"segments\[0\]\.seconds must be"),
        (
            {"segments": segs(seg(count=5000), seg(count=3199, at=1))},
            r"segment counts add up to 8199, more than the total of 8198",
        ),
        (
            {"segments": segs(seg(at=2), seg(at=1))},
            r"segments\[1\]\.at is before segments\[0\]\.at",
        ),
        (
            {"total": 0},
            r"total variable is missing or is not a whole number from 1 to 20000",
        ),
        (
            {"total": 20001},
            r"total variable is missing or is not a whole number from 1 to 20000",
        ),
        ({"total": 99.5}, r"total variable is missing or is not a whole number"),
        ({"total": "8198"}, r"total variable is missing or is not a whole number"),
        ({"title": ""}, r"title variable is empty"),
        ({"title": "x" * 71}, r"title is 71 characters; at most 70"),
        ({"title": 5}, r"title variable is missing or is not text"),
        ({"unit": "x" * 17}, r"unit is 17 characters; at most 16"),
        ({"note": "x" * 67}, r"note is 67 characters; at most 66"),
        ({"note": None}, r"note variable is missing or is not text"),
    ],
)
def test_a_bad_variable_is_refused_with_a_message_that_names_it(override, message):
    error = error_of(COLD_OPEN | override)

    assert error is not None, override
    assert error.startswith("bar-grid: ")
    assert re.search(message, error), error


@needs_node
def test_the_limits_themselves_are_accepted():
    assert error_of(variables([seg(count=1, label="x" * 38)], title="t" * 70, total=1)) is None
    assert error_of(variables([seg(count=20000)], total=20000)) is None
    assert error_of(variables([seg(count=1, at=i) for i in range(5)])) is None
    assert error_of(variables([seg(count=8198)], unit="", note="")) is None
    # a list passed by the host (not JSON text) is used as it is
    out = run(
        BLOCK,
        f"emit(readConfig({js(COLD_OPEN | {'segments': [seg(count=8198)]})}).segments.length);",
    )
    assert out == 1


@needs_node
def test_a_missing_segments_variable_is_refused():
    v = {k: val for k, val in COLD_OPEN.items() if k != "segments"}

    assert re.search(r"segments variable is missing", error_of(v))


@needs_node
def test_segments_get_their_place_in_the_grid_in_order_and_their_trail():
    cfg = run(BLOCK, f"emit(readConfig({js(FOR_SCALE)}).segments);")

    assert [s["first"] for s in cfg] == [0, 3600]
    assert [s["count"] for s in cfg] == [3600, 320]
    # a trail of half a second, and never more than half of the segment's own time
    assert [s["trail"] for s in cfg] == [0.5, 0.5]
    assert [s["sweep"] for s in cfg] == [pytest.approx(2.5), pytest.approx(0.7)]
    short = run(BLOCK, f"emit(readConfig({js(variables([seg(seconds=0.4)]))}).segments[0]);")
    assert short["trail"] == pytest.approx(0.2) and short["sweep"] == pytest.approx(0.2)


# -- the slot minimum ------------------------------------------------------------------------------


# The minimums of the two shots (7.3 s and 10.0 s), hand computed, and the block's refusal of a slot a hundredth under
# them are in test_preflight.py with the other blocks' (the DRIFT cases and test_bar_grid_is_the_last_fill_...).


@needs_node
def test_the_minimum_follows_the_segment_that_is_full_last_and_reads_its_label():
    long_first = [
        seg(count=10, label="the first segment is long", at=0, seconds=6),
        seg(count=10, label="short", at=1, seconds=2),
    ]
    short_first = [
        seg(count=10, label="short", at=0, seconds=1),
        seg(count=10, label="the second segment has this many words", at=2, seconds=2),
    ]
    tie = [
        seg(count=10, label="first one", at=0, seconds=4),
        seg(count=10, label="the later of two that end together", at=2, seconds=2),
    ]

    for segments in (long_first, short_first, tie):
        assert slot(variables(segments)) == pytest.approx(hand_minimum(segments))
    # the first segment is the one read in the first case and the second in the other two
    assert slot(variables(long_first)) == pytest.approx(6 + 0.7 + 1.2 + 0.3 * 5 + 0.5)
    assert slot(variables(tie)) == pytest.approx(4 + 0.7 + 1.2 + 0.3 * 7 + 0.5)


@needs_node
def test_only_the_label_of_the_last_segment_is_read_not_the_title_the_unit_or_the_note():
    longer = variables([seg(count=8198)], title="t" * 70, note="n" * 60, unit="u" * 16)

    assert slot(longer) == slot(COLD_OPEN) == pytest.approx(7.3)
    # the default slot of the block holds its own minimum
    assert defaults()["durationSeconds"] >= slot(defaults())


@needs_node
def test_the_preflight_passes_the_blocks_own_refusal_on_for_variables_it_would_refuse():
    (result,) = preflight.minimum_slots([("bar-grid", variables([seg(tone="teal")]))], REGISTRY)

    assert result.seconds is None
    assert 'segments[0].tone "teal"' in result.error


@needs_node
def test_a_segment_that_ends_after_the_slot_is_refused_and_named():
    v = variables([seg(count=10, at=0, seconds=2), seg(count=10, at=3, seconds=4.5)])

    error = check_slot(v, 7.0)

    assert re.search(
        r"segments\[1\] ends at 7\.50 s \(at 3 plus seconds 4\.5\), after the slot of durationSeconds 7 s",
        error,
    )
    # the first segment, which ends in time, is not the one named
    assert "segments[0] ends" not in error


@needs_node
def test_a_slot_that_ends_every_fill_but_is_too_short_to_read_is_refused_with_the_blocks_prefix():
    assert check_slot(COLD_OPEN, 7).startswith("bar-grid: durationSeconds 7 is shorter than the 7.30 s it needs")
    assert check_slot(COLD_OPEN, 7.3) is None


# -- the registry ---------------------------------------------------------------------------------


def test_the_registry_entry_names_the_block_and_says_how_its_minimum_is_known():
    item = REGISTRY.get("bar-grid")

    assert item.duration == 8 == defaults()["durationSeconds"]
    assert item.slot_not_checked is None
    assert preflight.block_logic(item, REGISTRY) is not None
    assert not item.covers_frame
    assert [v["id"] for v in item.variables] == [v["id"] for v in declared()]
    assert "bar" in item.description and "legend" in item.description


# -- the documented example ------------------------------------------------------------------------


def documented_shots() -> list[dict]:
    doc = (REPO / "docs" / "motion-library.md").read_text(encoding="utf-8")
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", doc, re.DOTALL)]
    return next(b for b in blocks if "bar-grid" in json.dumps(b))["shots"]


def test_the_two_shots_in_the_docs_are_real_shots_that_pass_every_rule_of_the_composer(
    tmp_path,
):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "voice.wav").write_bytes(b"x")
    raw = {
        "version": 1,
        "meta": {"title": "Bar grid", "language": "en", "publish": True},
        "audio": {"voice": {"path": "audio/voice.wav"}},
        "shots": documented_shots(),
    }

    result = plan(parse_shotlist(raw), [], 20.0, assets_dir=tmp_path)

    assert [(layer.component, layer.start, layer.slot) for layer in result.layers] == [
        ("bar-grid", 0.0, 8.0),
        ("bar-grid", 8.0, 12.0),
    ]
    assert result.warnings == []
    # the shots in the docs are the two this file builds from (COLD_OPEN and FOR_SCALE)
    first, second = (s["layers"][0]["variables"] for s in documented_shots())
    for documented, built in ((first, COLD_OPEN), (second, FOR_SCALE)):
        wanted = {k: v for k, v in built.items() if k != "durationSeconds"}
        assert json.loads(documented.pop("segments")) == json.loads(wanted.pop("segments"))
        assert documented == wanted
