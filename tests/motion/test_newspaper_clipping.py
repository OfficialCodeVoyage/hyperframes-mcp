"""``newspaper-clipping``: the promises of the contract a static test can check, and what is specific to a clipping.

Specific to it: the provenance rule (a source label is required, the defaults are marked SAMPLE), the limits of the tilt
and the travel, the marker highlight, the halftone photo, the reach of the stage (tape, tilt and push-in stay inside
the safe zone) and the seeded torn edge. The edge generator is run under node when it is installed; the frames
are looked at in a render (docs/motion-library.md).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from functools import cache

import pytest

from hyperframes_mcp.motion import registry, tokens

BLOCK = tokens.MOTION_DIR / "blocks" / "newspaper-clipping.html"
COMPOSITION_ID = "newspaper-clipping"


@cache
def html() -> str:
    return BLOCK.read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The file without its leading licence comment."""
    return text.split("-->", 1)[1]


def declared() -> list[dict]:
    """The variables of the registry entry (a registry test keeps them equal to the ones the block declares)."""
    return list(registry.get(COMPOSITION_ID).variables)


def defaults() -> dict:
    return {v["id"]: v["default"] for v in declared()}


def angle() -> float:
    return float(re.search(r"var angle = (-?[\d.]+);", html()).group(1))


def push_scale() -> float:
    return float(re.search(r"scale: (1\.\d+), duration: duration - enter", html()).group(1))


@cache
def css_rule(selector: str) -> str:
    """The declarations of the first rule whose selector is exactly ``selector`` (after the ``#clip-root`` scope)."""
    text = html()
    for match in re.finditer(rf"#clip-root {re.escape(selector)} \{{([^}}]*)\}}", text, re.DOTALL):
        # a rule that starts here, not the second selector of a grouped rule ("a,\n b {")
        if text[: match.start()].rstrip().endswith(("}", "*/")):
            return match.group(1)
    raise AssertionError(f"no rule for {selector}")


def px(rule: str, prop: str) -> float:
    return float(re.search(rf"{prop}: (-?[\d.]+)px", rule).group(1))


def rgb(hexcode: str) -> tuple[float, ...]:
    return tuple(int(hexcode[i : i + 2], 16) for i in (1, 3, 5))


def as_hex(profiles) -> str:
    return "#" + "".join(f"{round(c):02X}" for c in profiles)


# -- the contract ---------------------------------------------------------------------------------


def test_it_registers_one_paused_timeline_under_its_composition_id():
    text = html()

    assert re.search(r'data-composition-id="([^"]+)"', text).group(1) == COMPOSITION_ID
    assert text.count("gsap.timeline(") == 1
    assert "paused: true" in text
    assert f'window.__timelines["{COMPOSITION_ID}"] = tl;' in text


def test_it_only_uses_fromto_and_no_literal_colour_or_shadow():
    text = code_of(html())

    assert ".fromTo(" in text
    assert not re.search(r"\.(?:to|from|set)\(", text)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", text)
    assert not re.search(r"\b(?:rgba?|hsla?)\(", text)
    assert "shadow" not in text.lower()
    assert "filter:" not in text


def test_it_takes_durationSeconds_first_and_declares_every_variable_of_the_brief():
    variables = declared()

    assert [v["id"] for v in variables] == [
        "durationSeconds",
        "sourceLabel",
        "dateline",
        "headline",
        "body",
        "image",
        "highlight",
        "edgeSeed",
        "side",
    ]
    by_id = {v["id"]: v for v in variables}
    assert by_id["side"]["type"] == "enum"
    assert [o["value"] for o in by_id["side"]["options"]] == ["left", "center", "right"]
    assert by_id["edgeSeed"]["type"] == "number" and by_id["edgeSeed"]["min"] == 0
    # the optional ones are off by default, so overriding the text alone can never trip over a leftover default
    assert by_id["image"]["default"] == ""
    assert by_id["highlight"]["default"] == ""


def test_the_default_slot_leaves_room_for_the_fades_and_the_reading_time():
    t = tokens.default_tokens()
    d = defaults()
    reading = max(
        t.duration["read_min"],
        t.duration["read_per_word"] * len((d["headline"] + " " + d["body"]).split()),
    )

    assert d["durationSeconds"] >= t.duration["enter"] + reading + t.duration["exit"]


def test_the_timeline_fills_the_slot_and_a_slot_too_short_to_read_fails_the_render():
    text = code_of(html())

    assert "duration - exit" in text  # the exit ends with the slot
    assert "duration - enter" in text  # the push-in ends with the slot
    assert "Math.max(readMin, readPerWord *" in text  # a hold of at least max(1.2 s, 0.3 s per word)
    assert "duration < needed" in text  # a slot shorter than the entrance, the reading time and the exit throws


def test_it_works_mounted_where_the_html_element_is_not_in_the_page():
    text = code_of(html())

    assert "getComputedStyle(clipping)" in text
    assert "if (raw)" in text
    assert "getVariables" in text
    assert 'document.querySelector("[data-composition-variables]")' not in text
    assert "getElementById" not in text


def test_it_builds_the_timeline_once_the_fonts_are_in_and_registers_it_only_then():
    """Layout depends on the fonts, which are not loaded when the script first runs (seen in a render)."""
    text = code_of(html())

    assert "document.fonts.load(" in text
    wait = text.index("Promise.all(loads).then(")
    assert text.index(f'window.__timelines["{COMPOSITION_ID}"]') > wait
    assert text.index("gsap.timeline(") > wait
    assert "clipping.offsetHeight > stage.clientHeight" in text  # the fit check
    assert "does not fit the safe zone" in text


# -- provenance -----------------------------------------------------------------------------------


def test_a_source_label_is_required_and_drawn_on_the_clipping():
    text = code_of(html())

    assert 'var sourceLabel = required("sourceLabel");' in text
    assert "is missing or empty" in text
    assert "sourceEl.textContent = sourceLabel;" in text
    assert re.search(r'<p class="source"></p>', text)


def test_the_defaults_are_marked_sample_and_name_no_real_masthead():
    d = defaults()

    assert d["sourceLabel"].startswith("Sample newspaper")
    for name in ("sourceLabel", "dateline", "headline", "body"):
        assert "SAMPLE" in d[name], name
        # no year, no real place or paper in the sample copy
        assert not re.search(r"\b1[0-9]{3}\b|\b20[0-9]{2}\b", d[name]), name


def test_a_photo_must_be_a_path_inside_the_project():
    text = code_of(html())

    assert "image must be a path inside the project" in text
    assert 'indexOf("..")' in text


# -- the look -------------------------------------------------------------------------------------


def test_it_uses_the_three_type_roles_at_the_token_sizes():
    assert "font-family: var(--font-label);" in css_rule(".source")
    assert "font-size: var(--size-credit);" in css_rule(".source")  # credit and source lines: 30 px
    assert "var(--font-label)" in css_rule(".dateline")
    assert "font-size: var(--size-min-text);" in css_rule(".dateline")
    assert "font-family: var(--font-display);" in css_rule(".headline")
    assert "font-weight: 700;" in css_rule(".headline")
    assert "font-size: var(--size-title);" in css_rule('.headline[data-size="large"]')
    assert "font-size: var(--size-subtitle);" in css_rule('.headline[data-size="medium"]')
    assert "font-family: var(--font-label);" in css_rule(".body")
    assert "font-size: var(--size-min-text);" in css_rule(".body")
    assert "font-family: var(--font-mono)" not in html()  # the typewriter face is for ledgers


def test_the_dateline_is_the_accent_on_paper_and_gold_is_only_the_marker():
    assert "color: var(--color-oxblood);" in css_rule(".dateline")
    assert "background: var(--color-gold);" in css_rule(".mb")
    assert "mix-blend-mode: multiply;" in css_rule(".mb")
    assert "gold" not in css_rule(".source") + css_rule(".headline") + css_rule(".body")


def test_ink_and_oxblood_stay_readable_on_the_toned_face_and_ink_on_the_marker():
    t = tokens.default_tokens()
    percent = int(
        re.search(
            r"color-mix\(in srgb, var\(--color-paper\) (\d+)%, var\(--color-paper-aged\)\)",
            css_rule(".face"),
        ).group(1)
    )
    paper, aged = rgb(t.colors["paper"]), rgb(t.colors["paper_aged"])
    face = as_hex([p * percent / 100 + a * (100 - percent) / 100 for p, a in zip(paper, aged)])
    gold = rgb(t.colors["gold"])
    marker = as_hex([g * f / 255 for g, f in zip(gold, rgb(face))])  # multiply

    assert tokens.contrast(t.colors["ink"], face) >= t.min_ratio
    assert tokens.contrast(t.colors["oxblood"], face) >= t.min_ratio
    assert tokens.contrast(t.colors["ink"], marker) >= t.min_ratio


def test_the_tilt_and_the_travel_stay_inside_the_motion_limits():
    text = code_of(html())
    rise = float(re.search(r"opacity: 0, y: ([\d.]+), rotation: angle \+ 1", text).group(1))
    lift = float(re.search(r"opacity: 0, y: (-[\d.]+), duration: exit", text).group(1))

    assert 0 < abs(angle()) <= 1.5 and abs(angle() + 1) <= 1.5  # at rest and while it settles
    assert rise <= 24 and abs(lift) <= 24
    assert "rotation: angle," in text  # the tween ends on the fixed angle


def test_one_slow_push_in_of_at_most_three_percent_and_no_ambient_motion():
    t = tokens.default_tokens()
    text = code_of(html())

    assert text.count("duration: duration - enter") == 1  # a single tween
    assert 1 < push_scale() <= 1.03
    shortest_hold = 2.4 - t.duration["enter"]  # the smallest durationSeconds
    assert math.log(push_scale()) / shortest_hold <= t.camera["max_zoom_per_s"]
    assert "repeat" not in text and "yoyo" not in text


def test_it_enters_with_the_enter_ease_exits_with_the_exit_ease_and_never_overshoots():
    t = tokens.default_tokens()
    text = code_of(html())

    assert 'ease: token("--ease-enter")' in text
    assert 'ease: token("--ease-exit")' in text
    assert 'ease: token("--ease-move")' in text
    assert 'token("--dur-enter")' in text and 'token("--dur-exit")' in text
    eases = set(re.findall(r'ease: "([^"]+)"', text))
    assert eases <= {"power1.inOut"}
    assert t.ease["enter"] == "power3.out" and t.ease["exit"] == "power2.in"


def test_the_marker_draws_left_to_right_with_scaleX_after_the_headline_can_be_read():
    text = code_of(html())

    assert "{ scaleX: 0 }" in text and "{ scaleX: 1," in text
    assert "transform-origin: 0% 50%;" in css_rule(".mb")
    assert "markDelay = Math.max(readMin, readPerWord * headlineWords)" in text
    assert "enter + markDelay," in text  # the sweep starts once the headline can be read
    # each word is measured, and the words of one line share one marker band (no seam where boxes meet)
    assert 'span("", "mw")' in text and 'span("", "mb")' in text
    assert "last.box.offsetTop === box.offsetTop" in text
    # a line after a line: the sweep stays inside the stagger total, and a line takes 70% of the enter time
    assert "staggerTotal / Math.max(1, marks.length - 1)" in text
    assert "markEach = enter * 0.7" in text
    assert "markDelay + staggerTotal + markEach + readMin" in text  # and the hold leaves room for it


def test_a_highlight_that_is_nowhere_in_the_text_fails_the_render():
    text = code_of(html())

    assert "is in neither the headline nor the body" in text


def test_the_photo_is_a_halftone_by_the_renderers_grading_and_is_never_tweened():
    text = code_of(html())
    grading = json.loads(re.search(r"'(\{\"effects\":.*?\}\})'", text, re.DOTALL).group(1))

    assert grading["effects"]["monoScreen"] == 1  # pure black dots; `halftone` leaves coloured specks
    assert "halftone" not in grading["effects"]
    # the graded <img> is never faded or moved: the whole clipping, a wrapper, is
    assert not re.search(r"fromTo\(\s*(?:photo|marks\b.*photo)", text)
    assert "photoImg.decode()" in text  # a photo that does not load fails the render
    assert "mix-blend-mode: multiply;" in css_rule(".photo")
    assert "background: var(--color-ink);" in css_rule(".photo::after")
    assert "mix-blend-mode: lighten;" in css_rule(".photo::after")


def test_the_text_is_set_through_textcontent_never_as_markup():
    text = code_of(html())

    assert "innerHTML" not in text and "insertAdjacentHTML" not in text
    assert "outerHTML" not in text


# -- the safe zone --------------------------------------------------------------------------------


def test_the_reach_covers_the_tape_the_tilt_and_the_push_in_even_for_a_full_height_clipping():
    """Everything the viewer sees stays inside the safe zone, also for a clipping as tall as the stage."""
    t = tokens.default_tokens()
    stage = re.search(r"#clip-root \.stage \{([^}]*)\}", html(), re.DOTALL).group(1)
    for side in ("top", "right", "bottom", "left"):
        assert f"calc(var(--safe-{side}) + var(--clip-reach))" in stage

    reach = float(re.search(r"--clip-reach: ([\d.]+)px", html()).group(1))
    tape = css_rule(".tape")
    width, height = px(tape, "width"), px(tape, "height")
    scale, tilt = push_scale(), math.radians(abs(angle()))
    card_width = px(
        re.search(r'#clip-root \.clipping\[data-photo="yes"\] \{([^}]*)\}', html(), re.DOTALL).group(1),
        "width",
    )
    stage_height = t.canvas["height"] - 2 * (t.safe["top"] + reach)

    def overshoot(rule: str) -> tuple[float, float]:
        """How far a tape strip, rotated about its centre, sticks out of the paper corner it is taped on."""
        turn = math.radians(abs(float(re.search(r"rotate\((-?[\d.]+)deg\)", rule).group(1))))
        half_x = (width / 2) * math.cos(turn) + (height / 2) * math.sin(turn)
        half_y = (width / 2) * math.sin(turn) + (height / 2) * math.cos(turn)
        into_x = -px(rule, "left" if "left:" in rule else "right") + width / 2
        into_y = px(rule, "top" if "top:" in rule else "bottom") + height / 2
        return half_x - into_x, half_y - into_y

    out_a = overshoot(css_rule(".tape-a"))
    out_b = overshoot(css_rule(".tape-b"))
    tape_x = max(out_a[0], out_b[0])
    tape_y = max(out_a[1], out_b[1])
    push_x, push_y = (scale - 1) * card_width / 2, (scale - 1) * stage_height / 2
    tilt_x = (stage_height / 2) * math.sin(tilt)
    tilt_y = (card_width / 2) * math.sin(tilt)

    assert tape_x + push_x + tilt_x <= reach
    assert tape_y + push_y + tilt_y <= reach
    # and the widest clipping (the photo layout) fits the stage
    assert card_width <= t.canvas["width"] - 2 * (t.safe["left"] + reach)


# -- the seeded torn edge -------------------------------------------------------------------------

NODE = shutil.which("node")
SEEDS = 40


EDGE_HARNESS = """
function run(edgeSeed) {
  var rimEl = { style: {} };
  var faceEl = { style: {} };
  /* GENERATOR */
  return { rim: rimEl.style.clipPath, face: faceEl.style.clipPath };
}
var out = {};
for (var seed = 0; seed < SEEDS; seed += 1) out[seed] = run(seed);
out.again = run(7);
console.log(JSON.stringify(out));
"""


def edge_script() -> str:
    """The generator of the torn edge, taken out of the block, wrapped to print the polygons of several seeds."""
    text = html()
    last = 'faceEl.style.clipPath = polygon("face");'
    generator = text[text.index("function mulberry32(seed)") : text.index(last) + len(last)]
    return EDGE_HARNESS.replace("/* GENERATOR */", generator).replace("SEEDS", str(SEEDS))


@pytest.fixture(scope="module")
def edges(tmp_path_factory) -> dict:
    if NODE is None:
        pytest.skip("node is not installed")
    script = tmp_path_factory.mktemp("edge") / "edge.js"
    script.write_text(edge_script(), encoding="utf-8")
    done = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(done.stdout)


POINT = re.compile(r"calc\(([\d.]+)% \+ (-?[\d.]+)px\) calc\(([\d.]+)% \+ (-?[\d.]+)px\)")


def points(polygon: str) -> list[tuple[float, float, float, float]]:
    return [tuple(map(float, m)) for m in POINT.findall(polygon)]


def test_the_same_seed_always_gives_the_same_edge_and_other_seeds_differ(edges):
    assert edges["7"] == edges["again"]
    assert len({edges[str(s)]["face"] for s in range(SEEDS)}) == SEEDS


def test_the_torn_edge_keeps_its_band_and_stays_clear_of_the_text(edges):
    sheet = re.search(r"padding: (\d+)px (\d+)px (\d+)px;", css_rule(".sheet"))
    padding = min(int(g) for g in sheet.groups())

    def depth(xp: float, xo: float, yp: float, yo: float) -> float:
        """How far a vertex on the box edge lies inside the box, along that edge's normal."""
        return max(abs(xo) if xp in (0, 100) else 0, abs(yo) if yp in (0, 100) else 0)

    for seed in range(SEEDS):
        rim, face = points(edges[str(seed)]["rim"]), points(edges[str(seed)]["face"])
        assert len(rim) == len(face) >= 200
        for rim_point, face_point in zip(rim, face, strict=True):
            assert rim_point[::2] == face_point[::2]  # the same place along the edge
            depth_rim, depth_face = depth(*rim_point), depth(*face_point)
            # the face lies deeper than the rim's outer boundary: a visible paper-core band
            assert depth_face > depth_rim >= 2 - 1e-9
            # and never so deep that the torn edge reaches the text
            assert depth_face < padding


# -- the registry ---------------------------------------------------------------------------------


def test_the_registry_entry_records_the_sources_it_studied():
    item = registry.get(COMPOSITION_ID)

    assert item.type == "hyperframes:block"
    assert item.source is not None
    assert item.source.sha == "568dc2ae5bbc0fc7f3acce5b3f6e745f7b1c670c"
    assert "torn-paper-rip" in item.source.url
    assert "0d30152a82b9ceb93cfdd9bdbf46f0d5ab3cde86" in item.notes  # the student kit
    third_party = (tokens.MOTION_DIR / "THIRD_PARTY.md").read_text(encoding="utf-8")
    assert "0d30152a82b9ceb93cfdd9bdbf46f0d5ab3cde86" in third_party
    assert "freeze-frame-dressing" in third_party
