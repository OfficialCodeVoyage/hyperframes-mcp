"""The ``date-stamp`` block: the contract and the promises that a static test can check."""

from __future__ import annotations

import re
from functools import partial

import pytest

from hyperframes_mcp.motion import lint, tokens
from tests.motion.block_runner import (
    code_of,
    declared_variables,
    default_variables,
    needs_node,
    over_white,
    read_block,
    run_block,
)

BLOCK = tokens.MOTION_DIR / "blocks" / "date-stamp.html"
html = partial(read_block, BLOCK)
declared = partial(declared_variables, BLOCK)
defaults = partial(default_variables, BLOCK)


def test_it_passes_the_linter():
    assert lint.lint_file(BLOCK) == []


def test_it_registers_one_timeline_under_its_name_with_fromto_only_and_no_shadow():
    text = code_of(html())

    assert text.count("gsap.timeline(") == 1
    assert 'window.__timelines["date-stamp"] = tl;' in text
    assert not re.search(r"\.(?:to|from|set)\(", text)
    assert not re.search(r"text-shadow|box-shadow|drop-shadow|filter\s*:", text)


def test_the_date_is_gold_display_type_with_lining_tabular_figures():
    text = html()

    assert "font-family: var(--font-display);" in text
    assert "font-size: var(--size-title);" in text
    assert "color: var(--color-gold);" in text
    assert "font-variant-numeric: lining-nums tabular-nums;" in text
    assert ("gold", "night") in tokens.default_tokens().text_pairs
    # the numbers are the rule's: nothing in the display face may fall under the minimum text size
    assert tokens.default_tokens().sizes["title"] >= tokens.default_tokens().sizes["min_text"]


def test_the_rule_draws_with_scalex_and_the_date_is_one_clip_path_reveal_left_to_right():
    text = code_of(html())

    assert re.search(r"\{ scaleX: 0 \},\s*\{ scaleX: 1,", text)
    assert "transform-origin: 0 50%;" in text
    assert re.search(r'\{ clipPath: "inset\(-12px 100% -12px 0%\)" \}', text)
    assert re.search(r'clipPath: "inset\(-12px -2% -12px 0%\)"', text)
    # one element revealed once: no per-character spans, timers or staggers
    assert not re.search(r'split\(""\)|stagger\s*:|createElement\("span"\)|\.ch\b', text)
    assert "textContent = text" in text


def test_it_enters_with_the_enter_ease_and_leaves_faster_with_the_exit_ease():
    text = code_of(html())
    t = tokens.default_tokens()

    assert 'ease: token("--ease-enter")' in text
    assert 'ease: token("--ease-exit")' in text
    assert 'ease: token("--ease-move")' in text  # the rule is drawn, not entered
    assert t.duration["exit"] <= 0.75 * t.duration["enter"]  # faster: about 70% of the enter
    assert text.count("duration: enter") == 3 and text.count("duration: exit") == 1
    # the fade out ends with the slot
    assert "duration - exit" in text


def test_the_group_staggers_by_one_micro_step_which_is_inside_the_budget():
    text = code_of(html())
    t = tokens.default_tokens()

    assert 'seconds("--dur-micro")' in text
    assert re.search(r"\},\s*step,\s*\);", text)  # the date starts one step in
    assert t.duration["micro"] <= t.duration["group_stagger_total_max"]


def test_the_hold_is_at_least_the_reading_time_and_a_short_slot_throws():
    text = code_of(html())
    t = tokens.default_tokens()
    d = defaults()
    words = len(d["text"].split(" "))
    reading = t.duration["read_min"] + t.duration["read_per_word"] * words
    needed = t.duration["micro"] + t.duration["enter"] + reading + t.duration["exit"]

    assert d["durationSeconds"] >= needed
    assert "needed" in text and "throw new Error" in text
    # the declared minimum covers the shortest date (one word)
    one_word = (
        t.duration["micro"]
        + t.duration["enter"]
        + t.duration["read_min"]
        + t.duration["read_per_word"]
        + t.duration["exit"]
    )
    assert declared()[0]["min"] >= one_word - 1e-9
    assert t.duration["read_min"] >= 1.2


def test_it_sits_in_a_corner_inside_the_safe_zone():
    text = html()

    assert "left: var(--safe-left);" in text
    assert "bottom: var(--safe-bottom);" in text
    assert "top: var(--safe-top);" in text
    assert "right: var(--safe-right);" in text
    assert "max-width: calc(100% - var(--safe-left) - var(--safe-right));" in text
    position = next(v for v in declared() if v["id"] == "position")
    assert [o["value"] for o in position["options"]] == [
        "bottom-left",
        "top-left",
        "top-right",
        "bottom-right",
    ]
    # each corner pins both of its sides to the safe margins
    for corner, sides in {
        "bottom-left": ("left", "bottom"),
        "top-left": ("left", "top"),
        "top-right": ("right", "top"),
        "bottom-right": ("right", "bottom"),
    }.items():
        rule = re.search(rf'\.stamp\[data-position="{corner}"\] \{{([^}}]*)\}}', text).group(1)
        assert all(f"{side}: var(--safe-{side});" in rule for side in sides), corner


def test_gold_stays_readable_on_the_plate_even_over_a_white_frame():
    """Gold on the plate's flat core: 4.5:1 or better with the plate at its CSS opacity over pure white."""
    t = tokens.default_tokens()
    core = [int(p) for p in re.findall(r"var\(--color-scrim\) (\d+)%, transparent\) 0%", html())]
    assert core, "the plate has no flat core stop"
    alpha = core[0] / 100
    plate = over_white(t.colors["scrim"], alpha)

    assert tokens.contrast(t.colors["gold"], plate) >= t.min_ratio
    # the core runs unbroken for most of the radius and then falls away to nothing
    assert re.search(r"var\(--color-scrim\) \d+%, transparent\) 70%", html())
    assert re.search(r"\n\s+transparent 100%\n", html())


def test_the_variables_are_declared_in_order_with_a_five_second_default():
    assert [v["id"] for v in declared()] == ["durationSeconds", "text", "position"]
    assert defaults()["durationSeconds"] == 5


def test_a_missing_variable_throws_instead_of_drawing_a_wrong_frame():
    text = code_of(html())

    assert "the text variable is missing or empty" in text
    assert "position must be bottom-left, top-left, top-right or bottom-right" in text
    assert "is shorter than the" in text


# -- the whole script, run in node against the real GSAP -----------------------------------------


def run(variables=None, after="return null;", **options):
    return run_block(BLOCK, variables, after=after, root="#stamp-root", **options)


TIMELINE = r"""
const kids = tl.getChildren(false, true, false);
const spans = kids.map((k) => [k.startTime(), k.startTime() + k.duration()]);
let busiest = 0;
for (const [start] of spans) {
  busiest = Math.max(busiest, spans.filter(([a, b]) => a <= start && start < b).length);
}
const at = (t) => { tl.time(t); return { stamp: els[".stamp"].opacity, scrim: els[".scrim"].opacity, rule: els[".rule"].scaleX, clip: els[".date"].clipPath }; };
return {
  duration: tl.duration(), tweens: kids.length, starts: spans.map((s) => s[0]).sort((a, b) => a - b),
  busiest, first: at(0), middle: at(tl.duration() / 2), end: at(tl.duration()),
  text: els[".date"].textContent, position: els[".stamp"].attrs["data-position"],
};
"""


@needs_node
def test_the_timeline_fills_the_slot_exactly_and_starts_and_ends_hidden():
    for seconds in (3.6, 5, 9):
        out = run({"durationSeconds": seconds}, TIMELINE)

        assert out["error"] is None, out
        shot = out["result"]
        assert shot["duration"] == pytest.approx(seconds, abs=1e-9)
        assert shot["tweens"] == 4
        # frame 0: the plate and the rule are not drawn, the date is clipped away, the stamp is at full opacity
        assert shot["first"]["scrim"] == 0 and shot["first"]["rule"] == 0
        assert shot["first"]["clip"].startswith("inset(-12px 100%")
        assert shot["first"]["stamp"] == 1
        # in the hold everything is on; at the last frame the stamp has faded out
        assert shot["middle"]["scrim"] == 1 and shot["middle"]["rule"] == 1
        assert shot["middle"]["clip"].startswith("inset(-12px -2%")
        assert shot["end"]["stamp"] == 0


@needs_node
def test_the_group_staggers_by_one_step_and_never_moves_more_than_the_limit_at_once():
    t = tokens.default_tokens()
    shot = run(None, TIMELINE)["result"]

    assert shot["starts"][0] == 0
    assert shot["starts"][-2] == pytest.approx(t.duration["micro"])
    assert shot["starts"][-2] - shot["starts"][0] <= t.duration["group_stagger_total_max"]
    assert shot["busiest"] <= t.limits["max_simultaneous_moving"]
    assert shot["text"] == "April 12, 1945" and shot["position"] == "bottom-left"


@needs_node
def test_the_date_is_whole_on_screen_for_at_least_the_reading_time():
    t = tokens.default_tokens()
    shot = run({"durationSeconds": 5}, TIMELINE)["result"]
    words = len(defaults()["text"].split(" "))
    revealed = shot["starts"][-2] + t.duration["enter"]
    leaves = 5 - t.duration["exit"]

    assert leaves - revealed >= t.duration["read_min"] + t.duration["read_per_word"] * words


@needs_node
def test_the_variables_of_a_mount_reach_the_page_and_a_bad_one_fails_the_render():
    moved = run({"text": "  1 February 1945 ", "position": "top-right"}, TIMELINE)

    assert moved["error"] is None
    assert moved["result"]["text"] == "1 February 1945"
    assert moved["result"]["position"] == "top-right"
    bad = {
        "empty": ({"text": "   "}, "the text variable is missing or empty"),
        "position": (
            {"position": "centre"},
            "position must be bottom-left, top-left, top-right or bottom-right",
        ),
        "short": ({"durationSeconds": 3.2}, "is shorter than the 3.60 s it needs"),
        "long text": (
            {"text": "Late February to March 1945", "durationSeconds": 4},
            "is shorter than the 4.20 s it needs",
        ),
    }
    for name, (variables, message) in bad.items():
        out = run(variables)

        assert out["error"] and message in out["error"], (name, out)


@needs_node
def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    """A mounted block's <html> is not in the page: there is no declaration to read, only getVariables()."""
    plain = run(None, TIMELINE, mounted=True)
    moved = run({"position": "bottom-right"}, TIMELINE, mounted=True)

    assert plain["error"] is None and plain["result"]["duration"] == 5
    assert moved["error"] is None and moved["result"]["position"] == "bottom-right"
