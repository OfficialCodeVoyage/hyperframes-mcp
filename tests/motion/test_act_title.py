"""The ``act-title`` block: a full-screen act title on the night ground.

Static checks of the contract on the file, and the timing logic run for real in node against a stub document
(``tests/motion/block_harness.py``): the word-by-word reveal, the cascade, the hold, the slot check.
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest

from hyperframes_mcp.motion import registry
from tests.motion.block_harness import (
    ENTER,
    EXIT,
    GROUP_MAX,
    LINE_GAP,
    T,
    check_calm,
    check_max_lengths,
    check_mounted,
    check_static,
    declared_variables,
    defaults,
    entrance_end,
    html,
    rule_of,
    run_block,
    things_moving_at_once,
)

NAME = "act-title"
WORD_STEP = T.duration["word_stagger"]
HOLD_MIN = 2.5


def rule(selector: str) -> str:
    return rule_of(NAME, selector)


def words_of(**variables: str) -> int:
    """Words on the card: the kicker, the title and the subtitle."""
    return len(" ".join(variables.values()).split())


def needed(title_words: int, all_words: int) -> float:
    return entrance_end(title_words, WORD_STEP) + max(HOLD_MIN, T.duration["read_per_word"] * all_words) + EXIT


DEFAULT_WORDS = words_of(**{k: defaults(NAME)[k] for k in ("kicker", "title", "subtitle")})


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
    check_max_lengths(
        NAME,
        {"kicker": "MAX_KICKER", "title": "MAX_TITLE", "subtitle": "MAX_SUBTITLE"},
    )


def test_the_look_is_night_and_paper_with_a_gold_kicker_and_a_thin_gold_rule():
    assert "background: var(--color-night);" in rule(".at-ground")
    assert "color: var(--color-gold);" in rule(".at-kicker")
    assert "font-family: var(--font-label);" in rule(".at-kicker")
    assert "font-size: var(--size-label);" in rule(".at-kicker")
    assert "font-family: var(--font-display);" in rule(".at-title")
    assert "font-size: var(--size-title);" in rule(".at-title")
    assert "color: var(--color-paper);" in rule(".at-title")
    assert "font-style: italic;" in rule(".at-subtitle")
    assert "font-size: var(--size-subtitle);" in rule(".at-subtitle")
    assert "background: var(--color-gold);" in rule(".at-rule")
    assert int(re.search(r"height: (\d+)px", rule(".at-rule")).group(1)) <= 4
    for pair in (("gold", "night"), ("paper", "night"), ("paper_aged", "night")):
        assert pair in T.text_pairs
    assert "font-variant-numeric: tabular-nums;" in html(NAME)


def test_the_stack_sits_inside_the_safe_zone_and_an_empty_subtitle_takes_no_room():
    safe_width = T.canvas["width"] - T.safe["left"] - T.safe["right"]

    assert "inset: var(--safe-top) var(--safe-right) var(--safe-bottom) var(--safe-left);" in rule(".at-stack")
    assert "display: none;" in rule(".at-subtitle:empty")
    for selector in (".at-title", ".at-subtitle"):
        assert int(re.search(r"max-width: (\d+)px", rule(selector)).group(1)) <= safe_width


def test_the_declared_slot_minimum_is_the_shortest_entrance_a_two_and_a_half_second_hold_and_the_exit():
    slot = declared_variables(NAME)["durationSeconds"]
    shortest = needed(title_words=1, all_words=2)

    assert slot["min"] >= shortest
    assert slot["min"] - shortest < 0.1 + 1e-9
    assert slot["min"] <= slot["default"] <= slot["max"]


# -- run for real in node ---------------------------------------------------------------------------


def test_the_default_slot_builds_one_calm_timeline_that_fills_the_slot():
    run = run_block(NAME)

    check_calm(run, NAME, defaults(NAME)["durationSeconds"])


def test_the_default_slot_holds_the_finished_title_at_least_as_long_as_it_takes_to_read_it():
    run = run_block(NAME)
    leave = run.tween(".at")
    arrived = max(t.end for t in run.tweens if t != leave)

    assert leave.start - arrived >= HOLD_MIN
    assert leave.start - arrived >= T.duration["read_per_word"] * DEFAULT_WORDS - 1e-9


def test_the_ground_fades_in_the_kicker_the_title_and_then_the_rule_and_subtitle_follow():
    run = run_block(NAME)
    ground, kicker = run.tween(".at-ground"), run.tween(".at-kicker")
    words = [run.tweens_of(i)[0] for i in run.children_with_class("at-word")]
    rule, subtitle = run.tween(".at-rule"), run.tween(".at-subtitle")

    assert ground.frm == {"opacity": 0} and ground.start == 0
    assert kicker.start == pytest.approx(LINE_GAP)
    assert words[0].start == pytest.approx(2 * LINE_GAP)
    assert rule.start == subtitle.start == pytest.approx(words[-1].start + LINE_GAP)
    assert rule.frm == {"scaleX": 0} and rule.to["scaleX"] == 1
    assert all(t.duration == ENTER for t in (ground, kicker, rule, subtitle, *words))


def test_each_word_of_the_title_is_masked_by_clip_path_and_starts_a_word_stagger_after_the_one_before():
    run = run_block(NAME, {"title": "The Panic of 1873"})
    ids = run.children_with_class("at-word")
    tweens = [run.tweens_of(i)[0] for i in ids]

    assert [run.text_of(i) for i in ids] == ["The", "Panic", "of", "1873"]
    assert all(
        t.frm == {"clipPath": "inset(100% 0% 0% 0%)", "y": 16}
        and t.to["clipPath"] == "inset(0% 0% 0% 0%)"
        and t.to["y"] == 0
        for t in tweens
    )
    starts = [t.start for t in tweens]
    assert [b - a for a, b in pairwise(starts)] == pytest.approx([WORD_STEP] * 3)


@pytest.mark.parametrize("words", [1, 6, 7])
def test_a_long_title_squeezes_its_stagger_so_all_words_start_within_the_group_total(
    words,
):
    title = " ".join(f"w{i}" for i in range(words))
    run = run_block(NAME, {"title": title, "durationSeconds": 12})
    starts = [run.tweens_of(i)[0].start for i in run.children_with_class("at-word")]

    assert run.error is None
    assert len(starts) == words
    assert starts[-1] - starts[0] <= GROUP_MAX + 1e-9
    if 1 < words <= 6:
        assert starts[1] - starts[0] == pytest.approx(WORD_STEP)


def test_the_words_of_a_title_count_as_one_thing_in_motion_and_never_exceed_six_things():
    long_title = " ".join(f"word{i}" for i in range(10))
    run = run_block(NAME, {"title": long_title, "durationSeconds": 12})

    assert run.error is None
    assert things_moving_at_once(run) <= T.limits["max_simultaneous_moving"]


def test_the_exit_fades_the_whole_block_and_ends_with_the_slot():
    run = run_block(NAME, {"durationSeconds": 8.4})

    check_calm(run, NAME, 8.4)
    assert run.tween(".at") == run.tweens[-1]


def test_an_empty_subtitle_is_allowed_it_is_not_drawn_and_the_rule_still_is():
    run = run_block(NAME, {"subtitle": "", "durationSeconds": 7})
    subtitle = run.element(".at-subtitle")

    assert run.error is None
    assert run.text_of(subtitle) == ""
    assert run.tweens_of(subtitle) == []
    assert run.tween(".at-rule")


def test_the_slot_check_counts_every_word_with_a_two_and_a_half_second_floor():
    title_words = len(defaults(NAME)["title"].split())
    exact = needed(title_words, DEFAULT_WORDS)

    assert DEFAULT_WORDS * T.duration["read_per_word"] > HOLD_MIN
    assert run_block(NAME, {"durationSeconds": exact}).error is None
    assert "shorter than" in run_block(NAME, {"durationSeconds": exact - 0.05}).error

    # one word each and no subtitle: the 2.5 s floor decides
    short = {"kicker": "Part 1", "title": "Prologue", "subtitle": ""}
    floor = needed(1, words_of(**short))
    assert floor == pytest.approx(entrance_end(1, WORD_STEP) + HOLD_MIN + EXIT)
    assert run_block(NAME, {**short, "durationSeconds": floor}).error is None
    assert "shorter than" in run_block(NAME, {**short, "durationSeconds": floor - 0.05}).error


def test_text_is_set_trimmed_through_textcontent_and_never_as_markup():
    run = run_block(NAME, {"kicker": " <i>Part</i> 2 ", "title": " A  <b>B</b> "})

    assert run.error is None
    assert run.text_of(run.element(".at-kicker")) == "<i>Part</i> 2"
    assert [run.text_of(i) for i in run.children_with_class("at-word")] == [
        "A",
        "<b>B</b>",
    ]


@pytest.mark.parametrize(
    "override, message",
    [
        ({"kicker": ""}, "kicker variable is missing or empty"),
        ({"kicker": None}, "kicker variable is missing or empty"),
        ({"title": "  "}, "title variable is missing or empty"),
        ({"title": 3}, "title variable is missing or empty"),
        ({"subtitle": None}, "subtitle variable is missing or empty"),
        ({"subtitle": 3}, "subtitle variable is missing or empty"),
        ({"kicker": "x" * 25}, "kicker is 25 characters"),
        ({"title": "x" * 71}, "title is 71 characters"),
        ({"subtitle": "x" * 111}, "subtitle is 111 characters"),
    ],
)
def test_a_missing_or_too_long_variable_fails_the_render_it_has_no_fallback(override, message):
    assert message in run_block(NAME, override).error
