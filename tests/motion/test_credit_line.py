"""The reference component, ``credit-line``: it keeps every promise of the contract that a static test can check."""

from __future__ import annotations

import json
import re

from hyperframes_mcp.motion import lint, tokens

BLOCK = tokens.MOTION_DIR / "blocks" / "credit-line.html"


def html() -> str:
    return BLOCK.read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The file without its leading licence comment."""
    return text.split("-->", 1)[1]


def test_the_reference_component_passes_its_own_linter():
    assert lint.lint_file(BLOCK) == []


def test_it_registers_one_paused_timeline_under_its_composition_id():
    text = html()
    composition_id = re.search(r'data-composition-id="([^"]+)"', text).group(1)

    assert composition_id == "credit-line"
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert f'window.__timelines["{composition_id}"] = tl;' in text


def test_it_only_uses_fromto_and_no_literal_colour():
    text = code_of(html())

    assert ".fromTo(" in text
    assert not re.search(r"\.(?:to|from|set)\(", text)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text)
    assert not re.search(r"\b(?:rgba?|hsla?)\(", text)


def test_it_draws_label_type_at_credit_size_inside_the_safe_zone():
    text = html()

    assert "font-size: var(--size-credit);" in text
    assert "font-family: var(--font-label);" in text
    assert "left: var(--safe-left);" in text
    assert "bottom: var(--safe-bottom);" in text
    assert "right: var(--safe-right);" in text
    assert "max-width: calc(100% - var(--safe-left) - var(--safe-right));" in text


def test_it_fades_in_with_the_enter_ease_and_out_with_the_exit_ease():
    text = code_of(html())

    assert 'ease: token("--ease-enter")' in text
    assert 'ease: token("--ease-exit")' in text
    # the durations are read from the tokens through seconds(), which the slot check (minSlot) shares
    assert 'seconds("--dur-enter")' in text
    assert 'seconds("--dur-exit")' in text
    # a fade only: the style guide has no sliding lower thirds
    assert not re.search(r"\b[xy]\s*:", text)


def test_the_text_stays_readable_even_over_a_white_frame():
    """Paper on the scrim: 4.5:1 or better with the scrim at its CSS opacity over pure white, the worst case."""
    t = tokens.default_tokens()
    percent = int(re.search(r"var\(--color-scrim\) (\d+)%", html()).group(1))
    alpha = percent / 100
    scrim = t.colors["scrim"]
    profiles = [int(scrim[i : i + 2], 16) * alpha + 255 * (1 - alpha) for i in (1, 3, 5)]
    over_white = "#" + "".join(f"{round(c):02X}" for c in profiles)

    assert tokens.contrast(t.colors["paper"], over_white) >= t.min_ratio
    assert ("paper", "scrim") in t.text_pairs


def test_the_default_slot_leaves_room_for_the_fades_and_the_reading_time():
    """The default durationSeconds is at least enter + reading time + exit, so the defaults render."""
    t = tokens.default_tokens()
    declared = json.loads(re.search(r"data-composition-variables='(\[.*?\])'", html(), re.DOTALL).group(1))
    default = {v["id"]: v["default"] for v in declared}
    words = len(default["text"].split(" "))
    reading = t.duration["read_min"] + t.duration["read_per_word"] * words

    assert default["durationSeconds"] >= (t.duration["enter"] + reading + t.duration["exit"])


def test_a_slot_too_short_to_read_fails_the_render_and_the_fade_out_ends_with_the_slot():
    text = code_of(html())

    assert "needed" in text and "throw new Error" in text
    assert "duration - exit" in text


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    """A mounted block's <html> is not in the page and its script sees a scoped document.

    The first version read its declaration through the document and never registered its timeline when
    mounted (``sub_timeline_readiness_timeout``), so the guard is static: tokens come from its own element,
    the declaration is read only when it is in the page, and the variables come from ``getVariables()``.
    """
    text = code_of(html())

    assert "getComputedStyle(creditEl)" in text
    assert "if (raw)" in text
    assert "getVariables" in text
    assert 'document.querySelector("[data-composition-variables]")' not in text
    assert "getElementById" not in text
    assert "document.documentElement.getAttribute(" in text
