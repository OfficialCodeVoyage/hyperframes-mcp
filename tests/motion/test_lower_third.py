"""The ``lower-third`` block: a paper band with a double rule, a name and a role, in a bottom corner.

Static checks of the contract on the file, and the timing logic run for real in node against a stub document
(``tests/motion/block_harness.py``): the slot check, the entrance, the hold, the exit.
"""

from __future__ import annotations

import re

import pytest

from hyperframes_mcp.motion import registry, tokens
from tests.motion.block_harness import (
    ENTER,
    EXIT,
    LINE_GAP,
    T,
    blend,
    check_calm,
    check_max_lengths,
    check_mounted,
    check_static,
    declared_variables,
    defaults,
    html,
    rule_of,
    run_block,
)

NAME = "lower-third"
ENTRANCE_END = 2 * LINE_GAP + ENTER
HOLD_MIN = T.duration["lower_third_hold_min"]


def rule(selector: str) -> str:
    return rule_of(NAME, selector)


# -- static: the contract on the file ---------------------------------------------------------------


def test_it_keeps_the_upstream_licence_notice():
    text = html(NAME)

    assert "Apache License, Version 2.0" in text
    assert registry.get(NAME).source.sha in text


def test_it_registers_one_paused_timeline_and_only_uses_fromto_and_no_literal_colour():
    check_static(NAME)


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    check_mounted(NAME)


def test_the_longest_text_the_variables_allow_is_the_one_the_script_enforces():
    check_max_lengths(NAME, {"name": "MAX_NAME", "role": "MAX_ROLE"})


def test_the_name_is_display_type_and_the_role_label_type_at_token_sizes():
    name, role = rule(".lt-name"), rule(".lt-role")

    assert "font-family: var(--font-display);" in name
    assert "font-size: var(--size-subtitle);" in name
    assert "color: var(--color-ink);" in name
    assert "font-family: var(--font-label);" in role
    assert "font-size: var(--size-label);" in role
    assert "color: var(--color-oxblood);" in role
    # the figures of the years do not change width between two names
    assert "font-variant-numeric: tabular-nums;" in html(NAME)


def test_the_band_is_paper_with_a_double_rule_in_ink():
    rules = rule(".lt-rules")

    assert "var(--color-paper)" in rule(".lt-paper")
    assert "border-top:" in rules and "border-bottom:" in rules
    assert rules.count("var(--color-ink)") == 2


def test_the_corner_is_inside_the_safe_zone_and_the_band_cannot_outgrow_it():
    text = html(NAME)
    width_max = int(re.search(r"--lt-width-max:\s*(\d+)px", text).group(1))

    assert "left: var(--safe-left);" in text
    assert "bottom: var(--safe-bottom);" in text
    assert "right: var(--safe-right);" in text
    assert T.safe["left"] + width_max <= T.canvas["width"] - T.safe["right"]
    assert "max-width: var(--lt-width-max);" in text


def test_the_text_is_readable_on_the_band_even_over_a_black_frame():
    """The band is paper at 92% over the video; over pure black it is darkest, so ink and oxblood are at their worst."""
    percent = int(re.search(r"var\(--color-paper\) (\d+)%", html(NAME)).group(1))
    over_black = blend(T.colors["paper"], "#000000", percent / 100)

    assert ("ink", "paper") in T.text_pairs and ("oxblood", "paper") in T.text_pairs
    assert tokens.contrast(T.colors["ink"], over_black) >= T.min_ratio
    assert tokens.contrast(T.colors["oxblood"], over_black) >= T.min_ratio


def test_the_declared_slot_minimum_is_the_entrance_the_lower_third_hold_and_the_exit():
    slot = declared_variables(NAME)["durationSeconds"]

    assert slot["min"] == pytest.approx(round(ENTRANCE_END + HOLD_MIN + EXIT, 1), abs=1e-9)
    assert slot["max"] >= ENTRANCE_END + T.duration["lower_third_hold_max"] + EXIT
    assert slot["min"] <= slot["default"] <= slot["max"]


def test_the_tallest_band_the_variables_allow_stays_in_the_lower_half_of_the_frame():
    """Worst case height from the CSS: a name on three lines and a role on two."""
    css = html(NAME)
    pad_top, pad_bottom = (int(n) for n in re.search(r"padding: (\d+)px var\(--lt-pad-x\) (\d+)px;", css).groups())
    name_line = T.sizes["subtitle"] * float(re.search(r"\.lt-name \{[^}]*line-height: ([\d.]+);", css).group(1))
    role_line = T.sizes["label"] * float(re.search(r"\.lt-role \{[^}]*line-height: ([\d.]+);", css).group(1))
    gap = int(re.search(r"gap: (\d+)px;", css).group(1))

    tallest = pad_top + 3 * name_line + gap + 2 * role_line + pad_bottom
    assert T.canvas["height"] - T.safe["bottom"] - tallest > T.canvas["height"] / 2, (
        "a three-line name and a two-line role would reach above the middle of the frame"
    )


# -- run for real in node ---------------------------------------------------------------------------


def test_the_default_slot_builds_one_calm_timeline_that_fills_the_slot():
    run = run_block(NAME)

    check_calm(run, NAME, defaults(NAME)["durationSeconds"])


def test_the_band_wipes_in_the_rule_draws_and_the_text_rises_a_few_pixels():
    run = run_block(NAME)
    wipe, draw = run.tween(".lt-paper"), run.tween(".lt-rules")
    name_in, role_in = run.tween(".lt-name"), run.tween(".lt-role")

    assert wipe.frm == {"clipPath": "inset(0% 100% 0% 0%)"}
    assert wipe.to["clipPath"] == "inset(0% 0% 0% 0%)"
    assert draw.frm == {"scaleX": 0} and draw.to["scaleX"] == 1
    assert wipe.start == draw.start == 0
    assert wipe.duration == draw.duration == ENTER
    assert name_in.frm == {"opacity": 0, "y": 16}
    assert role_in.frm == {"opacity": 0, "y": 16}
    assert name_in.start == pytest.approx(LINE_GAP)
    assert role_in.start == pytest.approx(2 * LINE_GAP)
    assert max(t.end for t in (wipe, draw, name_in, role_in)) == pytest.approx(ENTRANCE_END)


def test_the_exit_fades_the_whole_band_and_ends_with_the_slot():
    run = run_block(NAME, {"durationSeconds": 7.3})

    check_calm(run, NAME, 7.3)
    assert run.tween(".lt") == run.tweens[-1]


@pytest.mark.parametrize("slot", [5.7, 9.0])
def test_the_hold_between_entrance_and_exit_is_at_least_the_lower_third_minimum(slot):
    run = run_block(NAME, {"durationSeconds": slot})
    leave = run.tween(".lt")
    entrance = [t for t in run.tweens if t != leave]

    assert run.error is None
    assert leave.start - max(t.end for t in entrance) >= HOLD_MIN - 1e-9


def test_a_slot_too_short_for_entrance_hold_and_exit_throws():
    needed = ENTRANCE_END + HOLD_MIN + EXIT

    assert run_block(NAME, {"durationSeconds": needed}).error is None
    assert "durationSeconds 5.6 is shorter" in run_block(NAME, {"durationSeconds": 5.6}).error
    assert "shorter" in run_block(NAME, {"durationSeconds": "soon"}).error


def test_a_long_role_is_read_not_cut_the_hold_grows_with_the_words():
    """Fourteen words need 0.3 s a word: more than the minimum hold, so the slot that fits two words fails."""
    role = " ".join(["word"] * 12)
    words = len(("Jay Cooke " + role).split())

    assert words * T.duration["read_per_word"] > HOLD_MIN
    assert run_block(NAME, {"role": role, "durationSeconds": 5.7}).error is not None
    needed = ENTRANCE_END + words * T.duration["read_per_word"] + EXIT
    assert run_block(NAME, {"role": role, "durationSeconds": needed}).error is None


def test_the_side_variable_decides_the_corner_and_the_direction_of_the_wipe():
    left = run_block(NAME, {"side": "left"})
    right = run_block(NAME, {"side": "right"})

    assert left.elements[left.element(".lt")]["attrs"]["data-side"] == "left"
    assert right.elements[right.element(".lt")]["attrs"]["data-side"] == "right"
    assert right.tween(".lt-paper").frm["clipPath"] == "inset(0% 0% 0% 100%)"
    assert '.lt[data-side="right"]' in html(NAME)


def test_text_is_set_trimmed_through_textcontent_and_never_as_markup():
    run = run_block(NAME, {"name": "  <b>Jay</b> Cooke ", "role": "A & B"})

    assert run.error is None
    assert run.text_of(run.element(".lt-name")) == "<b>Jay</b> Cooke"
    assert run.text_of(run.element(".lt-role")) == "A & B"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"name": ""}, "name variable is missing or empty"),
        ({"name": None}, "name variable is missing or empty"),
        ({"role": "   "}, "role variable is missing or empty"),
        ({"role": 7}, "role variable is missing or empty"),
        ({"side": "centre"}, "side must be"),
        ({"side": None}, "side must be"),
        ({"name": "x" * 41}, "name is 41 characters"),
        ({"role": "x" * 65}, "role is 65 characters"),
    ],
)
def test_a_missing_empty_or_too_long_variable_fails_the_render_it_has_no_fallback(override, message):
    assert message in run_block(NAME, override).error
