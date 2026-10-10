"""The ``quote-card`` block: a primary-source quote on a paper card over the night ground.

Static checks of the contract on the file, the timing and line-breaking logic run for real in node against a stub
document (``tests/motion/block_harness.py``), and the fit of the longest allowed quote, measured with the advance
widths of the real font file.
"""

from __future__ import annotations

import functools
import re
from itertools import pairwise

import pytest

from hyperframes_mcp.motion import registry, tokens
from tests.motion.block_harness import (
    ENTER,
    EXIT,
    GROUP_MAX,
    LINE_GAP,
    T,
    blend,
    check_calm,
    check_max_lengths,
    check_mounted,
    check_static,
    code_of,
    declared_variables,
    defaults,
    entrance_end,
    html,
    rule_of,
    run_block,
)

NAME = "quote-card"
READ_MIN, READ_PER_WORD = T.duration["read_min"], T.duration["read_per_word"]
SIZE_PX = {
    "subtitle": T.sizes["subtitle"],
    "body": T.sizes["body"],
    "min-text": T.sizes["min_text"],
}
LONGEST = 220
# The quote block is at most this tall (px) and its lines are this many times the type size apart: five lines at the
# subtitle size, six at the body size, seven at the minimum text size.
MAX_QUOTE_HEIGHT = 420
LEADING = 1.25

SPEECH = (
    "The only thing we have to fear is fear itself — nameless, unreasoning, unjustified terror which "
    "paralyzes needed efforts to convert retreat into advance."
)
LETTER = (
    "The panic began when Jay Cooke and Company, the banking house that had financed the Union war effort and "
    "then the Northern Pacific Railway, closed its doors on the eighteenth of September, and the Stock Exchange "
    "shut for ten days."
)


def cut(text: str, limit: int) -> str:
    """The longest prefix of ``text`` that ends at a word and has at most ``limit`` characters."""
    out = ""
    for word in text.split():
        if len(f"{out} {word}".strip()) > limit:
            break
        out = f"{out} {word}".strip()
    return out


LONG_220 = cut(LETTER + " " + LETTER, LONGEST)
UPPER_220 = cut((LETTER + " " + LETTER).upper(), LONGEST)


def rule(selector: str) -> str:
    return rule_of(NAME, selector)


def css_number(name: str) -> int:
    return int(re.search(rf"{name}:\s*(\d+)px", html(NAME)).group(1))


def quote_lines(run) -> list[str]:
    return [run.text_of(i) for i in run.children_with_class("qc-line")]


def chosen_size(run) -> str:
    return run.elements[run.element(".qc-quote")]["attrs"]["data-size"]


# -- the font: advance widths read from the file ------------------------------------------------------


@functools.cache
def advances(opsz: int) -> dict[str, float]:
    """Advance of every character in em for Newsreader Italic at weight 400 and the given optical size."""
    pytest.importorskip("fontTools")
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    italic = next(f.file for f in T.fonts["display"].files if f.style == "italic")
    font = instantiateVariableFont(TTFont(tokens.FONTS_DIR / italic), {"opsz": opsz, "wght": 400}, inplace=False)
    upm = font["head"].unitsPerEm
    metrics = font["hmtx"]
    return {chr(code): metrics[name][0] / upm for code, name in font.getBestCmap().items()}


def real_width(text: str, px: int) -> float:
    """The width of ``text`` set at ``px`` px, from the font's advances at the optical size the browser picks, without
    kerning (kerning only tightens a line)."""
    table = advances(px)
    return sum(table[ch] for ch in text) * px


# -- static: the contract on the file ---------------------------------------------------------------


def test_it_keeps_the_upstream_licence_notice():
    text = html(NAME)

    assert "MIT License" in text and "Copyright (c) 2026 Nate Herk" in text
    assert registry.get(NAME).source.sha in text


def test_it_registers_one_paused_timeline_and_only_uses_fromto_and_no_literal_colour():
    check_static(NAME)


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    check_mounted(NAME)


def test_the_longest_text_the_variables_allow_is_the_one_the_script_enforces():
    text = code_of(html(NAME))

    check_max_lengths(
        NAME,
        {
            "quote": "MAX_QUOTE",
            "attribution": "MAX_ATTRIBUTION",
            "source": "MAX_SOURCE",
        },
    )
    assert declared_variables(NAME)["quote"]["maxLength"] == LONGEST
    assert int(re.search(r"MAX_QUOTE_HEIGHT = (\d+)", text).group(1)) == MAX_QUOTE_HEIGHT
    assert float(re.search(r"LEADING = ([\d.]+)", text).group(1)) == LEADING
    assert f"line-height: {LEADING};" in rule(".qc-quote")


def test_the_look_is_an_ink_italic_quote_on_a_paper_card_with_an_oxblood_mark_over_night():
    assert "background: var(--color-night);" in rule(".qc-ground")
    assert "background: var(--color-paper);" in rule(".qc-card")
    quote = rule(".qc-quote")
    assert "font-family: var(--font-display);" in quote
    assert "font-style: italic;" in quote
    assert "color: var(--color-ink);" in quote
    mark = rule(".qc-mark")
    assert "color: var(--color-oxblood);" in mark
    assert "font-family: var(--font-display);" in mark
    assert "&ldquo;" in html(NAME)
    attribution = rule(".qc-attribution")
    assert "font-family: var(--font-label);" in attribution
    assert "font-size: var(--size-label);" in attribution
    for pair in (("ink", "paper"), ("oxblood", "paper")):
        assert pair in T.text_pairs
    assert "font-variant-numeric: tabular-nums;" in html(NAME)


def test_the_quote_is_never_set_under_the_minimum_text_size():
    """The sizes the script picks from are tokens, each with its own CSS rule, and the smallest is the minimum text size."""
    names = re.search(r"var SIZES = \[(.*?)\];", code_of(html(NAME))).group(1)
    sizes = [n.strip().strip('"') for n in names.split(",")]

    assert sizes == ["subtitle", "body", "min-text"]
    for size in sizes:
        assert f"font-size: var(--size-{size});" in rule(f'.qc-quote[data-size="{size}"]')
    assert T.sizes["min_text"] == min(SIZE_PX.values()) == SIZE_PX[sizes[-1]]


def test_the_source_line_is_at_the_credit_size_and_readable_on_the_card():
    source = rule(".qc-source")
    mix = re.search(r"var\(--color-ink\) (\d+)%, var\(--color-paper\)", source)

    assert "font-size: var(--size-credit);" in source
    assert "font-family: var(--font-label);" in source
    assert mix
    blended = blend(T.colors["ink"], T.colors["paper"], int(mix.group(1)) / 100)
    assert tokens.contrast(blended, T.colors["paper"]) >= T.min_ratio


def test_the_stage_is_inside_the_safe_zone_and_the_card_cannot_outgrow_it_in_width():
    assert "inset: var(--safe-top) var(--safe-right) var(--safe-bottom) var(--safe-left);" in rule(".qc-stage")
    assert "max-width: calc(var(--qc-text-width) + 2 * var(--qc-pad-x));" in rule(".qc-card")
    card = css_number("--qc-text-width") + 2 * css_number("--qc-pad-x")
    assert card <= T.canvas["width"] - T.safe["left"] - T.safe["right"]


def test_the_advance_table_in_the_script_is_the_one_of_the_font_file():
    """Read from Newsreader Italic (weight 400, optical size 56): characters 32 to 126, and the typographic marks."""
    text = code_of(html(NAME))
    table = [
        int(n)
        for n in re.search(r"var ADVANCE = \[(.*?)\];", text, re.DOTALL)
        .group(1)
        .replace("\n", " ")
        .replace(" ", "")
        .strip(",")
        .split(",")
    ]
    extra = {
        char: int(width)
        for char, width in re.findall(
            r'"(.)": (\d+)',
            re.search(r"var ADVANCE_EXTRA = \{(.*?)\};", text, re.DOTALL).group(1),
        )
    }
    font = advances(56)

    assert len(table) == 95
    for code, width in enumerate(table, start=32):
        assert abs(width - font[chr(code)] * 1000) <= 0.51, chr(code)
    assert extra
    for ch, width in extra.items():
        assert abs(width - font[ch] * 1000) <= 0.51, repr(ch)


def test_the_declared_slot_minimum_is_the_shortest_entrance_the_reading_minimum_and_the_exit():
    slot = declared_variables(NAME)["durationSeconds"]
    shortest = entrance_end(1, LINE_GAP) + READ_MIN + EXIT

    assert slot["min"] >= shortest
    assert slot["min"] - shortest < 0.1 + 1e-9
    assert slot["min"] <= slot["default"] <= slot["max"]


def test_the_tallest_card_the_variables_allow_stays_inside_the_safe_zone():
    """The quote block at its tallest, attribution and source each on two lines: from the CSS."""
    css = html(NAME)
    pad_top, pad_bottom = (
        int(n) for n in re.search(r"\.qc-card \{[^}]*padding: (\d+)px var\(--qc-pad-x\) (\d+)px;", css).groups()
    )
    by_gap = int(re.search(r"\.qc-by \{[^}]*margin-top: (\d+)px", css).group(1))
    rule_h = int(re.search(r"\.qc-by-rule \{[^}]*height: (\d+)px", css).group(1))
    rule_gap = int(re.search(r"\.qc-by-rule \{[^}]*margin-bottom: (\d+)px", css).group(1))
    attribution = T.sizes["label"] * float(re.search(r"\.qc-attribution \{[^}]*line-height: ([\d.]+);", css).group(1))
    source = T.sizes["credit"] * float(re.search(r"\.qc-source \{[^}]*line-height: ([\d.]+);", css).group(1))
    source_gap = int(re.search(r"\.qc-source \{[^}]*margin-top: (\d+)px", css).group(1))

    tallest_by = rule_h + rule_gap + 2 * attribution + source_gap + 2 * source
    card = pad_top + MAX_QUOTE_HEIGHT + by_gap + tallest_by + pad_bottom

    assert card <= T.canvas["height"] - T.safe["top"] - T.safe["bottom"]


# -- run for real in node ---------------------------------------------------------------------------


def test_the_default_slot_builds_one_calm_timeline_that_fills_the_slot():
    run = run_block(NAME)

    check_calm(run, NAME, defaults(NAME)["durationSeconds"])
    assert " ".join(quote_lines(run)) == defaults(NAME)["quote"]


def test_the_card_fades_and_rises_without_a_scale_pop_and_the_mark_only_fades():
    run = run_block(NAME)
    card, mark = run.tween(".qc-card"), run.tween(".qc-mark")

    assert card.frm == {"opacity": 0, "y": 20}
    assert card.to["opacity"] == 1 and card.to["y"] == 0
    assert card.duration == ENTER
    assert mark.frm == {"opacity": 0}
    assert not [t for t in run.tweens if any("scale" in key for key in t.frm)]


def test_the_ground_comes_first_then_the_card_the_quote_lines_and_last_the_attribution():
    run = run_block(NAME)
    ground, card = run.tween(".qc-ground"), run.tween(".qc-card")
    lines = [run.tweens_of(i)[0] for i in run.children_with_class("qc-line")]
    by = run.tween(".qc-by")

    assert ground.start == 0 < card.start < lines[0].start
    assert by.start == pytest.approx(lines[-1].start + LINE_GAP)


@pytest.mark.parametrize(
    "quote",
    ["Fear itself.", SPEECH[:110].rsplit(" ", 1)[0], SPEECH, LONG_220],
)
def test_the_quote_lines_start_a_quarter_second_apart_squeezed_to_half_a_second_in_all(
    quote,
):
    run = run_block(NAME, {"quote": quote, "durationSeconds": 20})
    ids = run.children_with_class("qc-line")
    starts = [run.tweens_of(i)[0].start for i in ids]
    count = len(starts)

    assert run.error is None
    assert " ".join(quote_lines(run)) == quote
    if count > 1:
        step = min(LINE_GAP, GROUP_MAX / (count - 1))
        assert [b - a for a, b in pairwise(starts)] == pytest.approx([step] * (count - 1))
        assert starts[-1] - starts[0] <= GROUP_MAX + 1e-9
        if count <= 3:
            assert step == LINE_GAP
    assert run.tweens_of(ids[0])[0].frm == {
        "clipPath": "inset(100% 0% 0% 0%)",
        "y": 14,
    }


def test_the_hold_is_three_tenths_of_a_second_a_word_of_the_quote():
    run = run_block(NAME)
    leave = run.tween(".qc")
    arrived = max(t.end for t in run.tweens if t != leave)

    assert leave.start - arrived >= READ_PER_WORD * len(SPEECH.split()) - 1e-9


def test_a_slot_too_short_to_read_the_quote_throws_and_the_exact_slot_does_not():
    lines = len(quote_lines(run_block(NAME)))
    exact = entrance_end(lines, LINE_GAP) + READ_PER_WORD * len(SPEECH.split()) + EXIT

    assert run_block(NAME, {"durationSeconds": exact}).error is None
    assert "shorter than" in run_block(NAME, {"durationSeconds": exact - 0.1}).error


def test_a_very_short_quote_is_held_for_a_second_and_a_fifth_at_least():
    quote = "Fear itself."
    exact = entrance_end(1, LINE_GAP) + READ_MIN + EXIT

    assert len(quote.split()) * READ_PER_WORD < READ_MIN
    assert run_block(NAME, {"quote": quote, "durationSeconds": exact}).error is None
    assert "shorter than" in run_block(NAME, {"quote": quote, "durationSeconds": exact - 0.1}).error


# -- the fit of the longest quote -------------------------------------------------------------------


@pytest.mark.parametrize("quote", [LONG_220, UPPER_220, SPEECH, "Fear itself."])
def test_every_line_of_every_quote_is_narrower_than_the_card_in_the_real_font(quote):
    run = run_block(NAME, {"quote": quote, "durationSeconds": 60})
    size = SIZE_PX[chosen_size(run)]
    lines = quote_lines(run)

    assert run.error is None
    assert " ".join(lines) == quote
    assert len(lines) * LEADING * size <= MAX_QUOTE_HEIGHT
    for line in lines:
        assert real_width(line, size) <= css_number("--qc-text-width"), line


def test_the_longest_allowed_quote_fits_the_card_at_the_minimum_text_size_even_in_capitals():
    """220 characters at 48 px: in the real font they need under five lines of text, and in capitals under seven."""
    text_width = css_number("--qc-text-width")
    narrowest = T.sizes["min_text"]

    assert len(LONG_220) >= LONGEST - 10 and len(UPPER_220) >= LONGEST - 10
    assert real_width(LONG_220, narrowest) / text_width < 5
    assert real_width(UPPER_220, narrowest) / text_width < 6.5
    assert 7 * LEADING * narrowest <= MAX_QUOTE_HEIGHT < 8 * LEADING * narrowest
    # ordinary text keeps a larger size, capitals need the smallest; neither is set under the minimum
    sizes = {
        label: SIZE_PX[chosen_size(run_block(NAME, {"quote": q, "durationSeconds": 60}))]
        for label, q in (("text", LONG_220), ("capitals", UPPER_220))
    }
    assert sizes["capitals"] == T.sizes["min_text"] <= sizes["text"]


def test_the_size_steps_down_with_the_length_and_never_goes_below_the_minimum():
    order = ["subtitle", "body", "min-text"]
    sizes = [
        order.index(chosen_size(run_block(NAME, {"quote": cut(LETTER, n), "durationSeconds": 30})))
        for n in (30, 60, 100, 140, 180, 220)
    ]

    assert sizes == sorted(sizes)
    assert sizes[0] == 0 and sizes[-1] > 0


def test_a_word_wider_than_the_card_or_a_quote_over_the_limit_fails_the_render():
    unbreakable = "x" * 80
    too_long = cut(LETTER + " " + LETTER + " " + LETTER, LONGEST + 60)

    assert "does not fit the card" in run_block(NAME, {"quote": unbreakable}).error
    assert f"{len(too_long)} characters" in run_block(NAME, {"quote": too_long}).error
    assert "does not fit the card" in run_block(NAME, {"quote": "W" * 70}).error


def test_text_is_set_trimmed_through_textcontent_and_never_as_markup():
    run = run_block(
        NAME,
        {
            "quote": " <b>Fear</b> itself ",
            "attribution": " A & B ",
            "source": "<i>x</i>",
        },
    )

    assert run.error is None
    assert quote_lines(run) == ["<b>Fear</b> itself"]
    assert run.text_of(run.element(".qc-attribution")) == "A & B"
    assert run.text_of(run.element(".qc-source")) == "<i>x</i>"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"quote": ""}, "quote variable is missing or empty"),
        ({"quote": None}, "quote variable is missing or empty"),
        ({"attribution": "  "}, "attribution variable is missing or empty"),
        ({"attribution": None}, "attribution variable is missing or empty"),
        ({"source": ""}, "source variable is missing or empty"),
        ({"source": 5}, "source variable is missing or empty"),
        ({"attribution": "x" * 61}, "attribution is 61 characters"),
        ({"source": "x" * 91}, "source is 91 characters"),
    ],
)
def test_a_missing_empty_or_too_long_variable_fails_the_render_it_has_no_fallback(override, message):
    assert message in run_block(NAME, override).error
