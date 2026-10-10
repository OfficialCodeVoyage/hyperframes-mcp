"""The component linter: every code fires on its own small bad sample and on nothing else.

Each bad sample is the clean ``GOOD`` document with one change, so a sample that triggers a second code
is a bug in the linter or in the sample. The linter is static: it never runs the page.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperframes_mcp.motion.lint import CODES, Problem, lint_file, lint_text, lint_tree

GOOD = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="stylesheet" href="tokens.css" />
    <script src="vendor/gsap.min.js"></script>
    <style>
      #root {
        position: absolute;
        width: 1920px;
        height: 1080px;
        background: var(--color-night);
      }
      #root .credit {
        font-family: var(--font-label);
        font-size: var(--size-credit);
        color: var(--color-paper);
        opacity: 0;
      }
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="sample"
      data-width="1920"
      data-height="1080"
      data-start="0"
      data-duration="4"
      data-composition-variables='[{"id":"text","type":"string","label":"Text","default":"x"}]'
    >
      <p class="credit">x</p>
    </div>
    <script>
      (function () {
        var tl = gsap.timeline({ paused: true });
        tl.fromTo(".credit", { opacity: 0 }, { opacity: 1, duration: 0.7, ease: "power3.out" }, 0);
        window.__timelines = window.__timelines || {};
        window.__timelines["sample"] = tl;
      })();
    </script>
  </body>
</html>
"""


def changed(old: str, new: str) -> str:
    """GOOD with the one occurrence of ``old`` replaced by ``new``."""
    assert GOOD.count(old) == 1, f"{old!r} must occur once in GOOD"
    return GOOD.replace(old, new)


def codes(text: str) -> list[str]:
    return sorted(p.code for p in lint_text(text))


def with_js(line: str) -> str:
    """GOOD with one more line of script before the timeline is registered."""
    return changed("        var tl = gsap", f"        {line}\n        var tl = gsap")


def with_css(rule: str) -> str:
    return changed("    </style>", f"      {rule}\n    </style>")


# -- the clean sample ------------------------------------------------------------------------------


def test_the_clean_sample_has_no_problems():
    assert lint_text(GOOD) == []


def test_every_code_has_a_test_below():
    # Keep the list below in step with CODES: a new code needs its own bad sample.
    assert set(CODES) == {
        "NETWORK_URL",
        "RANDOM_OR_CLOCK",
        "BARE_GSAP",
        "NOT_PAUSED",
        "NO_TIMELINE_REGISTRATION",
        "TWEEN_FROM",
        "BANNED_EASE",
        "CSS_TRANSITION",
        "LAYOUT_TWEEN",
        "FILTER_TWEEN",
        "OFF_TOKEN_COLOR",
        "SMALL_TEXT",
        "FONT_NOT_IN_TOKENS",
        "MISSING_VARIABLES",
        "MISSING_ROOT_ATTRS",
        "ALLOW_WITHOUT_REASON",
    }


# -- NETWORK_URL -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        changed(
            '<script src="vendor/gsap.min.js">',
            '<script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js">',
        ),
        changed('href="tokens.css"', 'href="//cdn.example.com/tokens.css"'),
        with_css('@import url("https://fonts.googleapis.com/css2?family=Inter");'),
        with_css("#root .x { background: url(//cdn.example.com/paper.png); }"),
        with_css("@import '//cdn.example.com/a.css';"),
        with_js('var u = "http://example.com/data.json";'),
        with_js('fetch("https://example.com/x");'),
    ],
)
def test_a_network_reference_is_found(text):
    assert codes(text) == ["NETWORK_URL"]


def test_network_url_reports_the_line_of_the_reference():
    text = changed(
        '<script src="vendor/gsap.min.js">',
        '<script src="https://cdn.example.com/g.js">',
    )
    (problem,) = lint_text(text)
    assert problem.line == 6
    assert problem.code == "NETWORK_URL"
    assert "cdn.example.com" in problem.message


def test_namespace_uris_and_local_paths_are_fine():
    text = changed(
        '<p class="credit">x</p>',
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"></svg>',
    )
    assert lint_text(text) == []


def test_a_url_in_a_comment_is_not_a_reference():
    text = changed(
        "    <style>",
        "    <!-- from https://example.com/x -->\n    <style>\n      /* see https://example.com/y */",
    )
    text = text.replace("var tl = gsap", "// docs: https://example.com/z\n        var tl = gsap")
    assert lint_text(text) == []


# -- RANDOM_OR_CLOCK -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "Math.random()",
        "Date.now()",
        "performance.now()",
        "new Date()",
        "requestAnimationFrame(function () {})",
        "setTimeout(function () {}, 10)",
        "setInterval(function () {}, 10)",
    ],
)
def test_randomness_and_clocks_are_found(call):
    assert codes(with_js(f"var x = {call};")) == ["RANDOM_OR_CLOCK"]


def test_a_commented_out_clock_is_fine():
    assert lint_text(with_js("// Date.now() is never used")) == []
    assert lint_text(with_js("/* Math.random() neither */")) == []


def test_a_comment_opener_inside_a_script_string_does_not_hide_the_code_after_it():
    text = with_js('var s = "<!--"; var r = Math.random(); // -->')

    assert codes(text) == ["RANDOM_OR_CLOCK"]


def test_a_script_written_inside_an_html_comment_is_not_live_code():
    text = changed(
        "    <style>",
        "    <!-- <script>var r = Math.random();</script> -->\n    <style>",
    )

    assert lint_text(text) == []


# -- timeline rules --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'gsap.to(".credit", { opacity: 1, duration: 1 });',
        'gsap.from(".credit", { opacity: 0, duration: 1 });',
        'gsap.fromTo(".credit", { opacity: 0 }, { opacity: 1, duration: 1 });',
    ],
)
def test_bare_gsap_tweens_are_found(line):
    assert codes(with_js(line)) == ["BARE_GSAP"]


def test_gsap_set_and_timeline_are_allowed_on_the_gsap_object():
    assert lint_text(with_js('gsap.set(".credit", { opacity: 0 });')) == []


def test_a_timeline_must_be_paused():
    for call in (
        "gsap.timeline()",
        "gsap.timeline({})",
        "gsap.timeline({ paused: false })",
    ):
        assert codes(changed("gsap.timeline({ paused: true })", call)) == ["NOT_PAUSED"], call
    # paused anywhere in the options counts
    assert lint_text(changed("{ paused: true }", "{ defaults: { ease: 'power3.out' }, paused: true }")) == []


def test_a_timeline_must_be_registered_under_an_id():
    text = changed('        window.__timelines["sample"] = tl;\n', "")
    assert codes(text) == ["NO_TIMELINE_REGISTRATION"]
    # creating the registry object alone is not registering a timeline
    assert "window.__timelines = window.__timelines || {};" in text


def test_a_page_without_any_script_has_no_timeline():
    text = GOOD[: GOOD.index("    <script>\n")] + "  </body>\n</html>\n"
    assert codes(text) == ["NO_TIMELINE_REGISTRATION"]


def test_from_on_a_timeline_is_found_but_array_from_is_not():
    text = changed(
        '        tl.fromTo(".credit", { opacity: 0 }, { opacity: 1, duration: 0.7, ease: "power3.out" }, 0);',
        '        tl.from(".credit", { opacity: 0, duration: 0.7, ease: "power3.out" }, 0);',
    )
    assert codes(text) == ["TWEEN_FROM"]
    assert lint_text(with_js('var all = Array.from(document.querySelectorAll(".credit"));')) == []


def test_from_on_a_timeline_under_another_name_is_found():
    text = changed("var tl = gsap", "var master = gsap")
    text = text.replace("tl.fromTo", "master.from").replace(
        '{ opacity: 0 }, { opacity: 1, duration: 0.7, ease: "power3.out" }',
        '{ opacity: 0, duration: 0.7, ease: "power3.out" }',
    )
    text = text.replace('__timelines["sample"] = tl', '__timelines["sample"] = master')
    assert codes(text) == ["TWEEN_FROM"]


# -- BANNED_EASE -----------------------------------------------------------------------------------


def with_ease(ease: str) -> str:
    return changed('ease: "power3.out"', f'ease: "{ease}"')


@pytest.mark.parametrize(
    "ease",
    [
        "back.out(2)",
        "back.out",
        "elastic.out(1, 0.3)",
        "bounce.out",
        "steps(4)",
        "linear",
        "none",
        "circ.out",
        "sine.inOut",
        "sine.out",
        "power3.in",
        "expo.inOut(2)",
    ],
)
def test_a_banned_ease_is_found(ease):
    assert codes(with_ease(ease)) == ["BANNED_EASE"]


@pytest.mark.parametrize(
    "ease",
    [
        "power3.out",
        "power2.in",
        "power2.inOut",
        "expo.out",
        "power1.inOut",
        "power2.out",
        "power4.out",
        "expo.in",
        "expo.inOut",
    ],
)
def test_the_token_eases_and_the_listed_extras_are_allowed(ease):
    assert lint_text(with_ease(ease)) == []


def test_a_ticker_may_be_linear_when_it_says_so():
    ticker = changed('<p class="credit">x</p>', '<p class="credit" data-linear-ok>x</p>')
    ticker = ticker.replace('ease: "power3.out"', 'ease: "none"')
    assert lint_text(ticker) == []
    # the marker does not excuse the other banned eases
    assert codes(ticker.replace('ease: "none"', 'ease: "back.out(2)"')) == ["BANNED_EASE"]


def test_an_ease_set_through_defaults_is_checked_too():
    text = changed("{ paused: true }", '{ paused: true, defaults: { ease: "elastic.out" } }')
    assert codes(text) == ["BANNED_EASE"]


# -- CSS_TRANSITION --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "#root .x { transition: opacity 1s; }",
        "#root .x { transition-duration: 1s; }",
        "#root .x { animation: spin 1s infinite; }",
        "#root .x { animation-name: spin; }",
        "@keyframes spin { from { opacity: 0; } to { opacity: 1; } }",
    ],
)
def test_css_motion_is_found(rule):
    assert codes(with_css(rule)) == ["CSS_TRANSITION"]


def test_css_motion_in_an_inline_style_is_found():
    text = changed(
        '<p class="credit">x</p>',
        '<p class="credit" style="transition: opacity 1s">x</p>',
    )
    assert codes(text) == ["CSS_TRANSITION"]


# -- LAYOUT_TWEEN and FILTER_TWEEN -------------------------------------------------------------------


def with_tween(vars_: str) -> str:
    return changed("{ opacity: 1, duration: 0.7,", f"{{ opacity: 1, {vars_}, duration: 0.7,")


@pytest.mark.parametrize(
    "vars_",
    [
        "width: 300",
        "height: 40",
        "top: 10",
        "left: 10",
        "right: 10",
        "bottom: 10",
        "fontSize: 60",
        '"font-size": 60',
        "margin: 10",
        "marginTop: 10",
        "paddingLeft: 10",
    ],
)
def test_tweening_layout_is_found(vars_):
    assert codes(with_tween(vars_)) == ["LAYOUT_TWEEN"]


@pytest.mark.parametrize(
    "vars_",
    [
        "x: 40",
        "y: 40",
        "xPercent: -50",
        "yPercent: 100",
        "scale: 1.05",
        "rotation: 3",
        "clipPath: 'inset(0 0 0 0)'",
        "strokeDashoffset: 0",
        "attr: { width: 40, height: 4 }",
    ],
)
def test_transform_opacity_clip_and_svg_attributes_are_allowed(vars_):
    assert lint_text(with_tween(vars_)) == []


def test_layout_inside_the_start_object_of_a_fromto_is_found():
    text = changed("{ opacity: 0 }, {", "{ opacity: 0, width: 0 }, {")
    assert codes(text) == ["LAYOUT_TWEEN"]


@pytest.mark.parametrize(
    "vars_",
    ['filter: "blur(4px)"', "blur: 4", 'backdropFilter: "blur(4px)"'],
)
def test_tweening_a_filter_is_found(vars_):
    assert codes(with_tween(vars_)) == ["FILTER_TWEEN"]


# -- OFF_TOKEN_COLOR -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        with_css("#root .x { color: #ff00ff; }"),
        with_css("#root .x { color: #f0f; }"),
        with_css("#root .x { background: rgba(0, 0, 0, 0.5); }"),
        with_css("#root .x { border: 2px solid hsl(10 20% 30%); }"),
        with_css("#root .x { box-shadow: 0 2px 6px #00000080; }"),
        changed('<p class="credit">x</p>', '<p class="credit" style="color: #123456">x</p>'),
        changed('<p class="credit">x</p>', '<svg><rect fill="#123456"></rect></svg>'),
        with_js('document.querySelector(".credit").style.color = "#123456";'),
        with_js('var shadow = "0 1px 3px rgba(0,0,0,0.8)";'),
    ],
)
def test_a_colour_literal_that_is_not_a_token_is_found(text):
    assert codes(text) == ["OFF_TOKEN_COLOR"]


def test_a_colour_that_is_a_token_value_is_not_off_token():
    assert lint_text(with_css("#root .x { color: #efe6d2; border-color: #E6B94D; }")) == []


def test_ids_that_look_like_hex_and_url_fragments_are_not_colours():
    css = with_css("#fade .x { fill: url(#add); }")
    assert lint_text(css) == []
    assert lint_text(with_js('var el = document.querySelector("#fade");')) == []


def test_var_and_color_mix_are_the_way_to_get_a_tint():
    css = with_css("#root .x { background: color-mix(in srgb, var(--color-scrim) 72%, transparent); }")
    assert lint_text(css) == []


# -- SMALL_TEXT ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "#root .body { font-size: 47px; }",
        "#root .label { font-size: 30px; }",
        "#root .x { font: 20px/1.2 var(--font-label); }",
    ],
)
def test_text_under_the_minimum_is_found(rule):
    assert codes(with_css(rule)) == ["SMALL_TEXT"]


@pytest.mark.parametrize(
    "rule",
    [
        "#root .body { font-size: 48px; }",
        "#root .credit-line { font-size: 30px; }",
        "#root .source { font-size: 30px; }",
        "#root .x { font-size: var(--size-body); }",
    ],
)
def test_text_at_or_over_the_minimum_is_fine(rule):
    assert lint_text(with_css(rule)) == []


def test_credit_and_source_rules_still_have_a_floor():
    assert codes(with_css("#root .credit { font-size: 29px; }")) == ["SMALL_TEXT"]
    assert codes(with_css("#root .source-note { font-size: 24px; }")) == ["SMALL_TEXT"]


def test_small_text_in_an_inline_style_uses_the_elements_class():
    small = changed('<p class="credit">x</p>', '<p class="credit" style="font-size: 30px">x</p>')
    assert lint_text(small) == []
    small = changed('<p class="credit">x</p>', '<p class="name" style="font-size: 30px">x</p>')
    assert codes(small) == ["SMALL_TEXT"]


# -- FONT_NOT_IN_TOKENS ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "Georgia, serif",
        '"Inter", sans-serif',
        "Arial",
        '"Newsreader", Georgia, serif',
        "system-ui",
    ],
)
def test_a_font_that_is_not_a_token_family_is_found(value):
    assert codes(with_css(f"#root .x {{ font-family: {value}; }}")) == ["FONT_NOT_IN_TOKENS"]


@pytest.mark.parametrize(
    "value",
    [
        "var(--font-display)",
        "var(--font-mono)",
        '"Newsreader", serif',
        '"IBM Plex Sans Condensed", sans-serif',
        "'Courier Prime', monospace",
        "inherit",
    ],
)
def test_token_fonts_are_fine(value):
    assert lint_text(with_css(f"#root .x {{ font-family: {value}; }}")) == []


# -- MISSING_VARIABLES and MISSING_ROOT_ATTRS --------------------------------------------------------


def test_a_composition_needs_declared_variables():
    text = changed(
        """\n      data-composition-variables='[{"id":"text","type":"string","label":"Text","default":"x"}]'""",
        "",
    )
    assert codes(text) == ["MISSING_VARIABLES"]


def test_variables_may_be_declared_on_the_html_element_as_hyperframes_documents():
    declaration = (
        """\n      data-composition-variables='[{"id":"text","type":"string","label":"Text","default":"x"}]'"""
    )
    text = changed(declaration, "")
    text = text.replace(
        '<html lang="en">',
        """<html lang="en" data-composition-variables='[{"id":"text","type":"string","label":"Text","default":"x"}]'>""",
    )
    assert lint_text(text) == []


@pytest.mark.parametrize(
    "attr",
    [
        'data-composition-id="sample"',
        'data-width="1920"',
        'data-height="1080"',
        'data-start="0"',
        'data-duration="4"',
    ],
)
def test_the_root_needs_its_attributes(attr):
    text = changed(f"\n      {attr}", "")
    assert codes(text) == ["MISSING_ROOT_ATTRS"]


def test_the_root_must_be_the_token_canvas():
    assert codes(changed('data-width="1920"', 'data-width="1280"')) == ["MISSING_ROOT_ATTRS"]
    assert codes(changed('data-height="1080"', 'data-height="720"')) == ["MISSING_ROOT_ATTRS"]


def test_the_root_attributes_are_read_across_lines_and_quoted_values_with_angle_brackets():
    text = changed('"label":"Text"', '"label":"Text, shown as <b> and a > sign"')
    assert lint_text(text) == []


# -- lint-allow ------------------------------------------------------------------------------------


def test_a_line_can_opt_out_with_a_reason():
    css = with_css("#root .x { box-shadow: 0 1px 2px #000; } /* lint-allow: OFF_TOKEN_COLOR pure black shadow */")
    assert lint_text(css) == []
    html = changed(
        '<script src="vendor/gsap.min.js">',
        '<script src="https://cdn.example.com/g.js"> <!-- lint-allow: NETWORK_URL vendored copy -->',
    )
    assert lint_text(html) == []  # the marker sits on the same line as the reference


def test_a_marker_without_a_reason_is_a_problem_and_excuses_nothing():
    css = with_css("#root .x { box-shadow: 0 1px 2px #000; } /* lint-allow: OFF_TOKEN_COLOR */")
    assert codes(css) == ["ALLOW_WITHOUT_REASON", "OFF_TOKEN_COLOR"]
    html = changed(
        '<script src="vendor/gsap.min.js">',
        '<script src="https://cdn.example.com/g.js"> <!-- lint-allow: NETWORK_URL -->',
    )
    assert codes(html) == ["ALLOW_WITHOUT_REASON", "NETWORK_URL"]


def test_a_marker_excuses_only_its_own_code_and_its_own_line():
    css = with_css(
        "#root .x { color: #123456; background: url(//cdn.example.com/a.png); } "
        "/* lint-allow: OFF_TOKEN_COLOR reviewed */"
    )
    assert codes(css) == ["NETWORK_URL"]
    css = with_css(
        "#root .x { color: #123456; } /* lint-allow: OFF_TOKEN_COLOR reviewed */\n      #root .y { color: #654321; }"
    )
    assert [p.code for p in lint_text(css)] == ["OFF_TOKEN_COLOR"]
    assert lint_text(css)[0].line == next(i for i, ln in enumerate(css.splitlines(), 1) if "#654321" in ln)


# -- files and trees -------------------------------------------------------------------------------


def test_lint_file_reads_a_file_and_names_it(tmp_path: Path):
    path = tmp_path / "bad.html"
    path.write_text(with_js("var x = Math.random();"), encoding="utf-8")

    problems = lint_file(path)

    assert [p.code for p in problems] == ["RANDOM_OR_CLOCK"]
    assert problems[0].path == str(path)
    assert isinstance(problems[0], Problem)
    assert str(problems[0]).startswith(f"{path}:")


def test_lint_tree_walks_every_html_file(tmp_path: Path):
    (tmp_path / "blocks").mkdir()
    (tmp_path / "blocks" / "good.html").write_text(GOOD, encoding="utf-8")
    (tmp_path / "blocks" / "bad.html").write_text(with_css("#root .x { transition: all 1s; }"), encoding="utf-8")
    (tmp_path / "notes.md").write_text("Math.random()", encoding="utf-8")

    problems = lint_tree(tmp_path)

    assert [(Path(p.path).name, p.code) for p in problems] == [("bad.html", "CSS_TRANSITION")]


def test_problems_come_out_in_line_order():
    text = with_js("var a = Math.random();").replace("height: 1080px;", "height: 1080px; transition: all 1s;", 1)
    lines = [p.line for p in lint_text(text)]
    assert lines == sorted(lines) and len(lines) == 2
