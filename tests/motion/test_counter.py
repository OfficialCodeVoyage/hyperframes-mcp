"""The ``counter`` block: a count-up to a target whose displayed value is exact at every frame.

The static checks keep the promises of the contract; the logic checks run the block's own functions in Node
(``tests/motion/node_logic.py``): the number formatting, the time plan, the fit of the figure, and a real GSAP
timeline of the count, seeked forward, backward and the way the HyperFrames runtime seeks.
"""

from __future__ import annotations

import json
import re
from functools import cache, partial
from itertools import pairwise

import pytest

from hyperframes_mcp.motion import registry, tokens
from tests.motion import block_helpers, node_logic
from tests.motion.block_helpers import code_of
from tests.motion.node_logic import LogicError, call, needs_node, run

BLOCK = tokens.MOTION_DIR / "blocks" / "counter.html"
MINUS = "−"
html = partial(block_helpers.html, BLOCK)
declared = partial(block_helpers.declared, BLOCK)
defaults = partial(block_helpers.defaults, BLOCK)


def plan_input(duration: float, words: int) -> dict:
    d = tokens.default_tokens().duration
    return {
        "duration": duration,
        "enter": d["enter"],
        "exit": d["exit"],
        "cinMin": d["cinematic_min"],
        "cinMax": d["cinematic_max"],
        "readMin": d["read_min"],
        "readPerWord": d["read_per_word"],
        "stagger": 0.25,
        "words": words,
    }


def figure_input(t: tokens.Tokens) -> dict:
    """What figureSize needs besides the text: the type sizes and the width between the safe margins."""
    return {
        "affixSize": t.sizes["title"],
        "available": t.canvas["width"] - t.safe["left"] - t.safe["right"],
        "max": t.sizes["title"] * 2.5,
        "min": t.sizes["title"],
    }


def digits(text: str) -> float:
    return float(text.replace(",", "").replace(MINUS, "-"))


# -- static ---------------------------------------------------------------------------------------


def test_it_declares_its_variables_durationseconds_first_and_is_our_own():
    """That the registry repeats the declaration, lints clean and has the root duration is checked for every item."""
    assert [v["id"] for v in declared()] == [
        "durationSeconds",
        "from",
        "to",
        "prefix",
        "suffix",
        "decimals",
        "label",
        "thousandsSeparator",
    ]
    assert registry.get("counter").source is None


def test_it_registers_one_paused_timeline_and_tweens_only_with_fromto():
    text = code_of(html())

    assert 'data-composition-id="counter"' in text
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert 'window.__timelines["counter"] = tl;' in text
    assert ".fromTo(" in text
    # the linter forbids tl.from and bare gsap tweens; the rest of the timeline's methods are for this block to keep out
    assert not re.search(r"\btl\.(?:to|call|add)\(", text)


def test_the_count_is_a_proxy_whose_every_render_sets_textcontent():
    """A modifier, not onUpdate and not tl.call: both are callbacks, which a quiet seek never runs.

    A seek that suppresses callbacks and jumps past the end of the count would leave the text of the frame
    before, a few units short of the target (the first version of this test failed that way).
    """
    text = code_of(html())

    assert "modifiers: { value: show }" in text
    assert "textContent" in text
    assert "innerHTML" not in text and "insertAdjacentHTML" not in text
    assert "onUpdate" not in re.sub(r"//[^\n]*", "", text)
    # the counted thing is a plain object, never the element
    assert re.search(r"var proxy = \{ value: ", text)
    # frame rows set one by one with tl.set / tl.call would break on a backward seek
    assert not re.search(r"\.set\(\s*[a-zA-Z.]*(?:live|value)", text)


def test_the_count_runs_over_the_cinematic_tokens_with_the_emphasis_ease():
    text = code_of(html())

    # planInput reads them through tok, which the page binds to the computed style and the preflight to tokens.css
    assert 'tok("--dur-cinematic-min")' in text
    assert 'tok("--dur-cinematic-max")' in text
    assert re.search(r"addCount\(.*?ease: token\(\"--ease-emphasis\"\)", text, re.DOTALL)
    assert 'token("--ease-enter")' in text
    assert 'token("--ease-exit")' in text


def test_the_suffix_and_the_label_arrive_after_the_count_lands():
    text = code_of(html())

    assert re.search(r"suffixEl,.*?plan\.landAt", text, re.DOTALL)
    assert re.search(r"labelEl,.*?plan\.labelAt", text, re.DOTALL)


def test_the_settle_is_a_small_scale_tween_that_never_passes_one():
    text = code_of(html())
    settle = re.search(r"figureEl,\s*\{ scale: ([\d.]+) \},\s*\{ scale: ([\d.]+)", text)

    assert settle, "the figure has no scale tween"
    start, end = float(settle.group(1)), float(settle.group(2))
    assert 0.9 <= start < end == 1.0
    # it ends with the count, on the emphasis ease, and no tween touches the type size
    assert re.search(
        r"figureEl,.*?plan\.count, ease: token\(\"--ease-emphasis\"\) \},\s*plan\.countAt",
        text,
        re.DOTALL,
    )
    assert not re.search(r"font-?[sS]ize\s*:", text.split("var tl = ")[1])


def test_it_sets_numbers_in_tabular_figures_in_the_display_face_at_token_sizes():
    text = html()

    assert "font-variant-numeric: tabular-nums;" in text
    assert "font-family: var(--font-display);" in text
    assert "font-family: var(--font-label);" in text
    assert "FIGURE_SCALE = 2.5" in text and 'number("--size-title")' in text
    assert "font-size: var(--size-label);" in text


def test_the_figure_and_the_label_stay_readable_over_a_white_frame():
    """Gold and paper on the scrim at its CSS opacity over pure white, the worst case, are 4.5:1 or better."""
    min_ratio = tokens.default_tokens().min_ratio

    assert block_helpers.scrim_contrast(html(), "gold") >= min_ratio
    assert block_helpers.scrim_contrast(html(), "paper") >= min_ratio


def test_everything_the_viewer_reads_is_inside_the_safe_zone():
    text = html()

    assert "left: var(--safe-left);" in text
    assert "right: var(--safe-right);" in text
    assert "top: var(--safe-top);" in text
    assert "bottom: var(--safe-bottom);" in text


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    text = code_of(html())

    assert "getComputedStyle(rootEl)" in text
    assert "if (raw)" in text
    assert "getVariables" in text
    assert 'document.querySelector("[data-composition-variables]")' not in text
    assert "getElementById" not in text


def test_a_missing_variable_throws_instead_of_drawing_a_wrong_frame():
    text = code_of(html())

    for name in ("to", "from", "decimals"):
        assert f'needNumber("{name}")' in text, name
    for name in ("prefix", "suffix", "label"):
        assert f'needText(V, "{name}")' in text, name
    assert 'typeof V.thousandsSeparator !== "boolean"' in text
    assert "throw new Error" in text


# -- logic, run in Node --------------------------------------------------------------------------


@needs_node
@pytest.mark.parametrize(
    ("value", "decimals", "thousands", "expected"),
    [
        (8198, 0, True, "8,198"),
        (8198, 0, False, "8198"),
        (98.6, 1, False, "98.6"),
        (0, 1, False, "0.0"),
        (0.1 + 0.2, 1, False, "0.3"),
        (1234567.891, 2, True, "1,234,567.89"),
        (999.5, 0, True, "1,000"),
        (-1250, 0, True, f"{MINUS}1,250"),
        (-0.04, 1, True, "0.0"),
        (1000000, 0, True, "1,000,000"),
    ],
)
def test_the_value_is_formatted_exactly(value, decimals, thousands, expected):
    assert call(BLOCK, "formatValue", value, decimals, thousands) == expected


@needs_node
def test_words_are_counted_for_the_reading_time():
    assert call(BLOCK, "wordCount", "  of gold   bullion ") == 3
    assert call(BLOCK, "wordCount", "") == 0


@needs_node
def test_the_default_slot_gives_the_count_its_longest_run():
    d = defaults()
    words = call(BLOCK, "wordCount", d["label"]) + call(BLOCK, "wordCount", d["suffix"]) + 1
    plan = call(BLOCK, "countPlan", plan_input(d["durationSeconds"], words))
    t = tokens.default_tokens().duration
    reading = t["read_min"] + t["read_per_word"] * words

    assert plan["count"] == t["cinematic_max"]
    assert plan["countAt"] < t["enter"], "the count starts while the arrival is still easing in"
    assert plan["landAt"] == pytest.approx(plan["countAt"] + plan["count"])
    assert plan["labelAt"] > plan["landAt"]
    assert plan["labelAt"] - plan["landAt"] <= t["group_stagger_total_max"]
    assert plan["exitAt"] - plan["holdFrom"] >= reading


@needs_node
def test_a_tight_slot_shortens_the_count_but_never_below_the_cinematic_minimum():
    t = tokens.default_tokens().duration
    reading = t["read_min"] + t["read_per_word"] * 4
    # everything but the count: the count starts at half the arrival, then the unit and the label, the reading, the exit
    rest = t["enter"] / 2 + 0.25 + t["enter"] + reading + t["exit"]
    tight = call(BLOCK, "countPlan", plan_input(rest + 1.5, 4))

    assert tight["count"] == pytest.approx(1.5)
    assert t["cinematic_min"] <= tight["count"] < t["cinematic_max"]
    assert tight["exitAt"] - tight["holdFrom"] == pytest.approx(reading)


@needs_node
def test_a_slot_too_short_for_the_count_and_the_reading_time_throws():
    with pytest.raises(LogicError, match="durationSeconds"):
        call(BLOCK, "countPlan", plan_input(4.0, 6))


@needs_node
def test_the_declared_minimum_slot_runs_for_a_short_label():
    d = defaults()
    minimum = next(v["min"] for v in declared() if v["id"] == "durationSeconds")
    plan = call(BLOCK, "countPlan", plan_input(minimum, 2))

    assert plan["count"] >= tokens.default_tokens().duration["cinematic_min"]
    assert d["durationSeconds"] >= minimum


@needs_node
def test_the_figure_is_as_large_as_fits_and_never_under_the_title_size():
    t = tokens.default_tokens()
    base = figure_input(t)
    short = call(BLOCK, "figureSize", {**base, "text": "8,198", "prefix": "", "suffix": "bars"})
    long = call(
        BLOCK,
        "figureSize",
        {**base, "text": "2,760,000,000", "prefix": "", "suffix": "Reichsmarks"},
    )

    assert short == base["max"]
    assert t.sizes["title"] <= long < base["max"]
    with pytest.raises(LogicError, match="does not fit"):
        call(
            BLOCK,
            "figureSize",
            {
                **base,
                "text": "1,234,567,890,123,456",
                "prefix": "",
                "suffix": "pieces of eight",
            },
        )


@needs_node
def test_the_estimate_of_the_figure_width_is_never_narrower_than_the_font():
    """Measured on Newsreader at weight 500 and optical size 72, which is what the browser draws at these sizes."""
    advance = node_logic.newsreader_advances(500, 72)
    base = figure_input(tokens.default_tokens()) | {"prefix": "$"}
    cases = [
        ("8,198", "bars"),
        ("98.6", "%"),
        ("2,760,000,000", "marks"),
        ("123,456,789,012", "units"),
        ("−1,250.5", "pieces"),
    ]
    for text, suffix in cases:
        size = call(BLOCK, "figureSize", {**base, "text": text, "suffix": suffix})
        figure = sum(advance[c] for c in text) * size
        affixes = sum(advance[c] for c in "$" + suffix) * base["affixSize"]
        assert figure + affixes <= base["available"], text


@needs_node
def test_the_width_table_covers_every_character_a_figure_is_made_of():
    advance = node_logic.newsreader_advances(500, 72)

    for ch in f"0123456789,.{MINUS}":
        assert call(BLOCK, "figureEm", ch) >= advance[ch], ch


@needs_node
def test_a_count_down_is_sized_and_boxed_by_its_wider_end():
    both = {"decimals": 0, "thousands": True}

    assert call(BLOCK, "widestText", {"from": 1000000, "to": 0, **both}) == "1,000,000"
    assert call(BLOCK, "widestText", {"from": 0, "to": 8198, **both}) == "8,198"
    assert call(BLOCK, "widestText", {"from": -5, "to": 5, **both}) == f"{MINUS}5"


COUNT_SPECS = [
    {"from": 0, "to": 8198, "decimals": 0, "thousands": True},
    {"from": 0, "to": 98.6, "decimals": 1, "thousands": False},
    {"from": 1000, "to": 0, "decimals": 0, "thousands": True},
    {"from": -50, "to": 25.5, "decimals": 1, "thousands": False},
    {"from": 0.1, "to": 0.3, "decimals": 1, "thousands": False},
    {"from": 0, "to": 2760000000, "decimals": 0, "thousands": True},
]

SAMPLE_COUNT = """
const spec = SPEC;
const tl = gsap.timeline({ paused: true });
let current = null;
addCount(tl, Object.assign({ at: 0.35, duration: 2, ease: "expo.out" }, spec), (text) => { current = text; });
const initial = current;
const frames = [];
for (let f = 0; f <= 7 * 24; f += 1) {
  const t = f / 24;
  // the HyperFrames runtime: a quiet seek just after, then the real one
  tl.totalTime(t + 0.001, true);
  tl.totalTime(t, false);
  frames.push([t, current]);
}
const order = frames.map((_, i) => (i * 37) % frames.length);
const shuffled = {};
for (const i of order) {
  tl.totalTime(frames[i][0], false);
  shuffled[i] = current;
}
emit({ initial, frames, shuffled, expected: formatValue(spec.to, spec.decimals, spec.thousands),
       start: formatValue(spec.from, spec.decimals, spec.thousands) });
"""


@cache
def sampled(spec_json: str) -> dict:
    """One Node run of the count per spec, shared by the tests that read it."""
    return run(BLOCK, SAMPLE_COUNT.replace("SPEC", spec_json))


@needs_node
@pytest.mark.parametrize("spec", COUNT_SPECS, ids=lambda s: f"{s['from']}-to-{s['to']}")
def test_the_displayed_value_moves_one_way_and_ends_exactly_on_the_target(spec):
    out = sampled(json.dumps(spec))
    values = [digits(text) for _, text in out["frames"]]
    rising = spec["to"] >= spec["from"]

    assert out["initial"] == out["start"]
    assert all((b >= a if rising else b <= a) for a, b in pairwise(values))
    assert out["frames"][0][1] == out["start"], "before the count it shows the start value"
    assert out["frames"][-1][1] == out["expected"], "after the count it shows the target"
    # it counts, it does not jump: the first frame after the count starts is still short of the target
    first_counting = next(text for t, text in out["frames"] if t > 0.35)
    assert first_counting != out["expected"]


@needs_node
@pytest.mark.parametrize("spec", COUNT_SPECS, ids=lambda s: f"{s['from']}-to-{s['to']}")
def test_a_backward_or_scattered_seek_shows_the_same_value_as_playing_forward(spec):
    out = sampled(json.dumps(spec))

    for i, (_, text) in enumerate(out["frames"]):
        assert out["shuffled"][str(i)] == text, i


@needs_node
def test_every_frame_shows_a_whole_formatted_number_no_float_noise():
    out = sampled(json.dumps(COUNT_SPECS[1]))

    assert all(re.fullmatch(r"\d{1,3}\.\d", text) for _, text in out["frames"])


def test_the_logic_markers_exist_once():
    text = html()

    assert text.count("// logic:begin") == 1
    assert text.count("// logic:end") == 1
    assert node_logic.logic_of(BLOCK).count("document") == 0


@needs_node
def test_witness_onupdate_misses_the_landing_under_a_quiet_seek():
    """Why the count uses a modifier: with onUpdate the same seeks leave the text short of the target.

    Not a test of the block: it keeps the reason on record, and fails (so the block can be simplified) if GSAP ever
    runs callbacks on a suppressed seek.
    """
    program = """
    const proxy = { value: 0 };
    let shown = null;
    const tl = gsap.timeline({ paused: true });
    tl.fromTo(proxy, { value: 0 }, { value: 100, duration: 2, ease: "expo.out",
      onUpdate() { shown = proxy.value; } }, 0);
    for (let f = 0; f <= 4 * 24; f += 1) {
      tl.totalTime(f / 24 + 0.001, true);
      tl.totalTime(f / 24, false);
    }
    emit({ shown, proxy: proxy.value });
    """
    out = run(BLOCK, program)

    assert out["proxy"] == 100
    assert out["shown"] < 100
