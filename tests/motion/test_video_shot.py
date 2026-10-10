"""The ``video-shot`` block: the contract, and its own script run in node against the real GSAP and a stand-in page.

What a render does with the element (the frames HyperFrames extracts, the freeze, the loop, the grade) is shown by a real
render, not here; ``tests/motion/test_composer_integration.py`` runs it when ``HYPERFRAMES_MCP_RENDER_INTEGRATION=1``.
"""

from __future__ import annotations

import json
import re
from functools import partial

import pytest

from hyperframes_mcp.motion import lint, registry, tokens
from tests.motion.block_runner import (
    code_of,
    declared_variables,
    default_variables,
    needs_node,
    read_block,
    run_block,
)

BLOCK = tokens.MOTION_DIR / "blocks" / "video-shot.html"
PHOTO = tokens.MOTION_DIR / "blocks" / "photo-shot.html"
html = partial(read_block, BLOCK)
declared = partial(declared_variables, BLOCK)
defaults = partial(default_variables, BLOCK)


def video_tag() -> str:
    return re.search(r"<video\b.*?</video>", html(), re.DOTALL).group()


# -- the contract, as far as a static test can see it --------------------------------------------


def test_it_passes_the_linter():
    assert lint.lint_file(BLOCK) == []


def test_it_registers_one_paused_timeline_that_is_as_long_as_the_slot_and_moves_nothing():
    text = code_of(html())

    assert 'data-composition-id="video-shot"' in text
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert 'window.__timelines["video-shot"] = tl;' in text
    # the clip is the motion: the one mark on the timeline is a set at the end of the slot
    assert not re.search(r"\.(?:to|from|fromTo)\(", text)
    assert re.findall(r"tl\.set\(([^;]*)\);", text) == ["clipEl, { x: 0 }, duration"]


def test_the_script_never_plays_pauses_or_seeks_the_clip_and_never_fades_it():
    script = re.search(r"<script>.*", code_of(html()), re.DOTALL).group()

    for forbidden in (
        ".play(",
        ".pause(",
        "currentTime",
        "playbackRate",
        "requestVideoFrameCallback",
        "autoplay",
        "opacity",
        "volume",
    ):
        assert forbidden not in script, forbidden
    # the playback the renderer reads from the element, and nothing else about the clip
    assert re.findall(r'clipEl\.setAttribute\(\s*"([a-z-]+)"', script) == [
        "data-duration",
        "data-media-start",
        "loop",
        "data-color-grading",
        "src",
    ]
    assert 'if (hold === "loop") clipEl.setAttribute("loop", "");' in script


def test_the_clip_is_silent_and_is_never_an_audio_source():
    tag = video_tag()
    text = html()

    assert re.search(r"\bmuted\b", tag) and re.search(r"\bplaysinline\b", tag)
    assert 'data-volume="0"' in tag
    assert "data-has-audio" not in tag and "<audio" not in text
    assert "crossorigin" not in text and not re.search(r"\bautoplay\b", tag)


def test_the_markup_gives_the_renderer_a_src_to_place_and_no_duration_to_cap_the_clip():
    """The renderer moves a video to its mount's start only when it finds a src in the markup; a duration written there
    would cap every clip at it. Both were found by a real render (docs/motion-library.md, video-shot).
    The src is an empty data URL, so it is no file that HyperFrames' own lint could find missing."""
    tag = video_tag()

    assert re.search(r'\bsrc="data:video/mp4;base64,"', tag)
    assert 'data-start="0"' in tag and "data-duration" not in tag
    assert "data-media-start" not in tag and not re.search(r"\bloop\b", tag)
    assert "assets/" not in tag


def test_the_clip_is_drawn_to_cover_the_frame_at_full_size():
    css = re.search(r"#video-root \.clip-video \{(.*?)\}", html(), re.DOTALL).group(1)

    assert "object-fit: cover" in css
    assert "width: 1920px" in css and "height: 1080px" in css
    root = re.search(r"#video-root \{(.*?)\}", html(), re.DOTALL).group(1)
    assert "background: var(--color-night)" in root and "overflow: hidden" in root


def test_the_finish_is_applied_through_data_color_grading_with_the_tokens():
    text = code_of(html())

    assert 'clipEl.setAttribute(\n          "data-color-grading"' in text
    assert 'vignette: Number.parseFloat(token("--finish-vignette"))' in text
    assert 'grain: Number.parseFloat(token("--finish-grain"))' in text
    assert "adjust: adjust" in text


def test_the_scrim_is_the_token_gradient_of_photo_shot():
    own, photo = html(), read_block(PHOTO)
    scrim = next(v for v in declared() if v["id"] == "scrim")

    assert [o["value"] for o in scrim["options"]] == ["none", "left", "bottom"]
    assert scrim["default"] == "bottom"
    assert not re.search(r"text-shadow|box-shadow|drop-shadow", own)
    for kind in ("bottom", "left"):
        rule = re.search(rf'\.scrim\[data-scrim="{kind}"\] \{{.*?\n      \}}', own, re.DOTALL)
        photo_rule = re.search(rf'\.scrim\[data-scrim="{kind}"\] \{{.*?\n      \}}', photo, re.DOTALL)
        assert rule and rule.group() == photo_rule.group().replace("photo-root", "video-root")


def test_the_grade_limits_are_photo_shots():
    """The two blocks each carry the table of the shader's adjustments and their ranges; a grade valid for one is for both."""

    def limits(path):
        return re.search(r"var LIMITS = \{.*?\};", read_block(path), re.DOTALL).group()

    assert limits(BLOCK) == limits(PHOTO)


def test_the_variables_are_declared_in_order_with_the_bounds_the_shot_list_checks():
    by_id = {v["id"]: v for v in declared()}

    assert list(by_id) == [
        "durationSeconds",
        "video",
        "trimStart",
        "grade",
        "scrim",
        "hold",
    ]
    assert by_id["durationSeconds"]["default"] == 6
    # the slot's floor is the cinematic minimum of the tokens, and the declared minimum says the same
    assert by_id["durationSeconds"]["min"] == tokens.default_tokens().duration["cinematic_min"]
    assert (by_id["trimStart"]["default"], by_id["trimStart"]["min"]) == (0, 0)
    assert by_id["trimStart"]["max"] == 600
    assert [o["value"] for o in by_id["hold"]["options"]] == ["freeze", "loop"]
    assert by_id["hold"]["default"] == "freeze"
    assert by_id["video"]["maxLength"] == 200
    # grade is photo-shot's variable, so a grade written for a photograph reads the same on a clip
    photo = {v["id"]: v for v in declared_variables(PHOTO)}
    assert by_id["grade"] == photo["grade"] and by_id["scrim"] == photo["scrim"]


def test_it_is_registered_as_a_full_frame_block_that_needs_a_credit_line():
    item = registry.get("video-shot")

    assert item.covers_frame and item.needs_credit
    assert item.duration == 6 and item.slot_not_checked is None
    assert list(item.variables) == declared()


def test_no_clip_is_stored_in_the_library_and_the_default_is_a_sample_projects():
    suffixes = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    clips = [p.name for p in tokens.MOTION_DIR.rglob("*") if p.suffix.lower() in suffixes]
    default_video = defaults()["video"]
    registered = {e.path for item in registry.list_items() for e in item.files}
    notes = registry.get("video-shot").notes

    assert clips == []
    assert not (tokens.MOTION_DIR / default_video).exists()
    assert default_video not in registered
    assert "sample project supplies" in notes and default_video in notes


def test_a_missing_or_wrong_variable_throws_instead_of_drawing_a_wrong_frame():
    text = code_of(html())

    for message in (
        "the video variable is missing or empty",
        "trimStart must be a number of seconds, zero or more",
        "hold must be freeze or loop",
        "grade must be a JSON object",
        "is not a known adjustment in range",
        "scrim must be none, left or bottom",
        "is shorter than the",
        "is not valid JSON",
    ):
        assert message in text, message


# -- the whole script, run in node against the real GSAP -----------------------------------------


def run(variables=None, after="return null;", **options):
    return run_block(BLOCK, variables, after=after, root="#video-root", **options)


SHOT = r"""
const clip = els[".clip-video"], scrim = els[".scrim"];
return {
  duration: tl.duration(),
  attrs: clip.attrs,
  scrim: scrim.attrs["data-scrim"],
  marks: tl.getChildren(false, true, false).length,
};
"""


@needs_node
def test_the_timeline_ends_exactly_at_the_slot_at_any_length():
    for seconds in (1.2, 6, 9, 30, 60):
        out = run({"durationSeconds": seconds}, "return tl.duration();")

        assert out["error"] is None, out
        assert out["result"] == pytest.approx(seconds, abs=1e-9), seconds


@needs_node
def test_the_defaults_alone_set_the_clip_up_for_the_renderer():
    t = tokens.default_tokens()

    out = run(None, SHOT)

    assert out["error"] is None, out
    shot = out["result"]
    attrs = shot["attrs"]
    assert shot["marks"] == 1 and shot["duration"] == 6 and shot["scrim"] == "bottom"
    assert attrs["src"] == "assets/video/sample-clip.mp4"
    assert attrs["data-duration"] == "6" and attrs["data-media-start"] == "0"
    assert "loop" not in attrs  # freeze: the renderer holds the last frame
    grading = json.loads(attrs["data-color-grading"])
    assert grading["preset"] == "neutral" and grading["intensity"] == 1
    assert grading["details"]["vignette"] == t.finish["vignette"]
    assert grading["details"]["grain"] == t.finish["grain"]
    assert grading["adjust"] == json.loads(defaults()["grade"])


@needs_node
def test_a_mount_sets_the_slot_the_trim_and_the_loop_on_the_element():
    freeze = run(
        {"video": "assets/media/clips/a.mp4", "durationSeconds": 9, "trimStart": 2.5},
        SHOT,
    )["result"]["attrs"]
    loop = run(
        {
            "video": "assets/media/clips/a.mp4",
            "durationSeconds": 9,
            "trimStart": 2.5,
            "hold": "loop",
        },
        SHOT,
    )["result"]["attrs"]

    for attrs in (freeze, loop):
        assert attrs["src"] == "assets/media/clips/a.mp4"
        assert attrs["data-duration"] == "9" and attrs["data-media-start"] == "2.5"
    assert "loop" not in freeze and loop["loop"] == ""


@needs_node
def test_the_grade_and_the_finish_are_the_ones_photo_shot_gives_a_photograph():
    grade = {"exposure": 0.2, "contrast": -0.1, "temperature": 0.15}
    clip = run({"grade": grade}, SHOT)["result"]["attrs"]["data-color-grading"]
    photo = run_block(
        PHOTO,
        {"grade": grade},
        after='return els[".photo"].attrs["data-color-grading"];',
        root="#photo-root",
    )["result"]

    assert json.loads(clip) == json.loads(photo)


@needs_node
def test_the_grade_may_come_as_json_text_or_as_values_and_the_scrim_is_set():
    as_text = run({"grade": '{"exposure": 0.2}', "scrim": "left"}, SHOT)
    as_values = run({"grade": {"exposure": 0.2}, "scrim": "left"}, SHOT)

    assert as_text["error"] is None and as_text == as_values
    assert as_text["result"]["scrim"] == "left"
    assert json.loads(as_text["result"]["attrs"]["data-color-grading"])["adjust"] == {"exposure": 0.2}


@needs_node
def test_a_bad_variable_fails_the_render_with_a_message_that_names_it():
    bad = {
        "video": ({"video": ""}, "the video variable is missing or empty"),
        "video blank": ({"video": "   "}, "the video variable is missing or empty"),
        "video number": ({"video": 5}, "the video variable is missing or empty"),
        "trim negative": (
            {"trimStart": -1},
            "trimStart must be a number of seconds, zero or more, got -1",
        ),
        "trim text": ({"trimStart": "soon"}, "trimStart must be a number of seconds"),
        "trim empty": ({"trimStart": ""}, "trimStart must be a number of seconds"),
        "trim null": ({"trimStart": None}, "trimStart must be a number of seconds"),
        "hold": ({"hold": "stop"}, "hold must be freeze or loop, got stop"),
        "json": ({"grade": "{"}, "grade is not valid JSON"),
        "grade type": ({"grade": "[1]"}, "grade must be a JSON object"),
        "grade key": (
            {"grade": '{"gamma": 0.1}'},
            "grade.gamma is not a known adjustment",
        ),
        "grade range": (
            {"grade": '{"contrast": 3}'},
            "grade.contrast is not a known adjustment in range",
        ),
        "scrim": ({"scrim": "top"}, "scrim must be none, left or bottom"),
        "short": ({"durationSeconds": 1.1}, "is shorter than the 1.2 s"),
    }
    for name, (variables, message) in bad.items():
        out = run(variables)

        assert out["error"] and message in out["error"], (name, out)


@needs_node
def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    """A mounted block's <html> is not in the page: there is no declaration to read, only getVariables()."""
    out = run(None, "return tl.duration();", mounted=True)
    moved = run({"durationSeconds": 9, "hold": "loop", "trimStart": 1}, SHOT, mounted=True)

    assert out["error"] is None and out["result"] == 6
    assert moved["error"] is None
    assert moved["result"]["duration"] == 9
    assert moved["result"]["attrs"]["loop"] == ""
    assert moved["result"]["attrs"]["data-media-start"] == "1"


@needs_node
def test_a_tokens_css_that_did_not_load_fails_instead_of_guessing_a_finish():
    out = run_block(
        BLOCK,
        None,
        after="return null;",
        root="#video-root",
        css={"--finish-grain": ""},
    )

    assert out["error"] and "tokens.css is not loaded: --finish-grain" in out["error"]
