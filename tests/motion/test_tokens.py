"""The motion tokens: a closed schema, valid colours and numbers, contrast, and the CSS they become."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.motion.tokens import TokensError

REPO = Path(__file__).resolve().parents[2]


def raw_tokens() -> dict:
    return json.loads(tokens.TOKENS_PATH.read_text(encoding="utf-8"))


def load(tmp_path: Path, data: dict) -> tokens.Tokens:
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return tokens.load_tokens(path)


def test_the_shipped_tokens_load():
    t = tokens.load_tokens()

    # the canvas is the render contract (HyperFrames at 1080p, 24 fps), not a design choice
    assert (t.canvas["width"], t.canvas["height"], t.canvas["fps"]) == (1920, 1080, 24)
    assert tuple(t.colors) == tokens.COLOR_NAMES
    assert tuple(t.fonts) == tokens.FONT_ROLES


def test_every_section_says_where_its_numbers_came_from():
    for name, section in raw_tokens().items():
        assert section["_sources"].strip(), name


def test_every_text_colour_pair_passes_contrast():
    assert tokens.contrast_problems() == []


def test_contrast_is_the_wcag_ratio():
    assert tokens.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert tokens.contrast("#FFFFFF", "#FFFFFF") == pytest.approx(1.0)
    # The order does not matter, and the number matches the visual style guide (13.9:1).
    assert tokens.contrast("#1E1A16", "#EFE6D2") == pytest.approx(13.93, abs=0.01)
    assert tokens.contrast("#EFE6D2", "#1E1A16") == tokens.contrast("#1E1A16", "#EFE6D2")


def test_a_failing_pair_is_reported_unless_its_foreground_is_decorative(tmp_path):
    data = raw_tokens()
    data["colors"]["gold"] = "#3A3228"  # dark gold on night: unreadable
    bad = load(tmp_path, data)
    problems = tokens.contrast_problems(bad)
    assert [(p.fg, p.bg) for p in problems if p.fg == "gold"] == [
        ("gold", "night"),
        ("gold", "night_raised"),
    ]
    assert all(p.ratio < p.minimum == 4.5 for p in problems)

    data["contrast"]["decorative"].append("gold")
    exempt = load(tmp_path, data)
    assert tokens.contrast_problems(exempt) == []


@pytest.mark.parametrize("bad", ["#27211", "27211A", "#27211AFF", "#GGGGGG", "night", 0x27211A])
def test_a_bad_colour_fails(tmp_path, bad):
    data = raw_tokens()
    data["colors"]["night"] = bad

    with pytest.raises(TokensError, match=r"colors\.night"):
        load(tmp_path, data)


def test_an_unknown_key_fails_at_every_level(tmp_path):
    for mutate, where in (
        (lambda d: d.update(shadows={"_sources": "x"}), "shadows"),
        (lambda d: d["colors"].update(mauve="#AA00AA"), r"colors\.mauve"),
        (lambda d: d["sizes"].update(huge=200), r"sizes\.huge"),
        (lambda d: d["fonts"]["mono"].update(italic=True), r"fonts\.mono\.italic"),
        (lambda d: d["fonts"].update(script={}), r"fonts\.script"),
    ):
        data = copy.deepcopy(raw_tokens())
        mutate(data)
        with pytest.raises(TokensError, match=where):
            load(tmp_path, data)


def test_a_missing_key_fails(tmp_path):
    data = raw_tokens()
    del data["duration"]["enter"]
    with pytest.raises(TokensError, match=r"duration\.enter"):
        load(tmp_path, data)

    data = raw_tokens()
    del data["limits"]
    with pytest.raises(TokensError, match="limits"):
        load(tmp_path, data)


def test_a_section_without_its_sources_note_fails(tmp_path):
    data = raw_tokens()
    data["camera"]["_sources"] = " "
    with pytest.raises(TokensError, match=r"camera\._sources"):
        load(tmp_path, data)


@pytest.mark.parametrize("value", [0, -1, "0.7", True, None])
def test_numbers_must_be_positive_numbers(tmp_path, value):
    data = raw_tokens()
    data["duration"]["enter"] = value

    with pytest.raises(TokensError, match=r"duration\.enter"):
        load(tmp_path, data)


def test_sizes_respect_the_minimums(tmp_path):
    data = raw_tokens()
    data["sizes"]["body"] = 40  # below min_text 48
    with pytest.raises(TokensError, match=r"sizes\.body"):
        load(tmp_path, data)

    data = raw_tokens()
    data["sizes"]["credit"] = 24  # below min_credit 30
    with pytest.raises(TokensError, match=r"sizes\.credit"):
        load(tmp_path, data)


def test_ranges_must_be_ordered(tmp_path):
    data = raw_tokens()
    data["duration"]["cinematic_min"] = 3.0  # above cinematic_max 2.0
    with pytest.raises(TokensError, match="cinematic_min"):
        load(tmp_path, data)


def test_every_error_is_reported_together(tmp_path):
    data = raw_tokens()
    data["colors"]["night"] = "nope"
    data["duration"]["enter"] = 0
    with pytest.raises(TokensError) as caught:
        load(tmp_path, data)

    assert "colors.night" in str(caught.value)
    assert "duration.enter" in str(caught.value)


def test_a_contrast_pair_must_name_known_colours(tmp_path):
    data = raw_tokens()
    data["contrast"]["text_pairs"].append(["ink", "mauve"])
    with pytest.raises(TokensError, match="mauve"):
        load(tmp_path, data)


def test_css_carries_colours_sizes_safe_zone_durations_eases_and_fonts():
    t = tokens.default_tokens()
    css = tokens.css()

    assert css.startswith(":root {")
    # a name with underscores is written with hyphens; durations and eases carry no unit
    assert f"--color-night-raised: {t.colors['night_raised']};" in css
    assert f"--size-min-text: {t.sizes['min_text']}px;" in css
    assert f"--safe-bottom: {t.safe['bottom']}px;" in css
    assert f"--dur-lower-third-hold-min: {t.duration['lower_third_hold_min']:g};" in css
    assert f"--ease-enter: {t.ease['enter']};" in css
    assert f"--font-label: {t.fonts['label'].stack};" in css
    assert t.fonts["label"].stack.endswith(", sans-serif")


def test_a_number_is_written_without_trailing_zeros():
    data = raw_tokens()
    data["duration"]["enter"] = 0.5
    data["duration"]["exit"] = 2
    css = tokens.css(tokens.parse_tokens(data))

    assert "--dur-enter: 0.5;" in css
    assert "--dur-exit: 2;" in css


def test_the_stylesheet_declares_every_font_file_under_the_install_path():
    t = tokens.default_tokens()
    sheet = tokens.stylesheet()

    assert sheet.count("@font-face") == sum(len(f.files) for f in t.fonts.values())
    for font in t.fonts.values():
        for face in font.files:
            assert f'src: url("{tokens.FONT_TARGET_DIR}/{face.file}") format("truetype");' in sheet
            assert f"font-weight: {face.weight};" in sheet
            assert f"font-style: {face.style};" in sheet


def test_a_font_role_is_found_in_the_text_that_names_it():
    text = ".a { font-family: var(--font-mono); } .b { font-family: var(--font-label); }"

    assert tokens.font_roles_in(text) == ["label", "mono"]
    assert tokens.font_roles_in("font-family: var(--font-script)") == []


def test_every_font_file_and_licence_the_tokens_name_is_in_the_repository():
    for role, font in tokens.load_tokens().fonts.items():
        assert font.licence == "OFL-1.1", role
        assert (tokens.FONTS_DIR / font.licence_file).is_file(), font.licence_file
        for face in font.files:
            assert (tokens.FONTS_DIR / face.file).is_file(), face.file


def test_default_tokens_are_loaded_once():
    assert tokens.default_tokens() is tokens.default_tokens()
