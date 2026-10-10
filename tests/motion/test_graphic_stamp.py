"""The ``graphic-stamp`` block: the contract, and its script run in node against the real GSAP with a stand-in page, a
fake ``fetch`` and a small stand-in DOM (the real browser run is in the library's rendering notes)."""

from __future__ import annotations

import json
import re
from functools import partial
from typing import Any

import pytest

from hyperframes_mcp.motion import lint, registry, tokens
from hyperframes_mcp.motion.compose import compose
from hyperframes_mcp.motion.shotlist import parse_shotlist
from tests.motion.block_runner import (
    GSAP,
    code_of,
    css_properties,
    declared_variables,
    default_variables,
    needs_node,
    read_block,
    run_node,
)

BLOCK = tokens.MOTION_DIR / "blocks" / "graphic-stamp.html"
html = partial(read_block, BLOCK)
declared = partial(declared_variables, BLOCK)
defaults = partial(default_variables, BLOCK)
T = tokens.default_tokens()

STROKED = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    '<path d="M3 21l18 0"/><path d="M3 10l18 0"/><circle cx="12" cy="12" r="3"/></svg>'
)
FILLED = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 15 15" fill="currentColor"><path d="M1 3h13v8H1z"/></svg>'
)


# -- static: the contract ---------------------------------------------------------------------------


def test_it_passes_the_linter_and_the_registry_repeats_its_variables():
    item = registry.default_registry().items["graphic-stamp"]

    assert lint.lint_file(BLOCK) == []
    assert list(item.variables) == declared()
    assert item.duration == defaults()["durationSeconds"] == 4
    assert not item.covers_frame and not item.needs_credit
    assert item.license == "Apache-2.0"


def test_the_variables_are_declared_in_order_with_the_minimum_slot_of_the_three_entrances():
    first = declared()[0]

    assert [v["id"] for v in declared()] == [
        "durationSeconds",
        "svg",
        "position",
        "size",
        "color",
        "animation",
    ]
    # every entrance ends at the enter time, so one static minimum is each one's exact minimum
    assert first["min"] == pytest.approx(T.duration["enter"] + T.duration["read_min"] + T.duration["exit"])
    assert defaults()["svg"] == "assets/media/vectors/stamp.svg"


def test_it_registers_one_timeline_under_its_name_with_fromto_only_and_only_allowed_properties():
    text = code_of(html())

    assert text.count("gsap.timeline(") == 1
    assert 'window.__timelines["graphic-stamp"] = tl;' in text
    assert not re.search(r"\.(?:to|from)\(", text)
    assert not re.search(r"text-shadow|box-shadow|drop-shadow|filter\s*:", text)
    for name in ("opacity", "scale", "rotation", "strokeDashoffset", "clipPath"):
        assert f"{name}:" in text


def test_every_colour_option_is_a_palette_token_and_has_a_rule_that_uses_it():
    text = html()
    options = [o["value"] for o in next(v for v in declared() if v["id"] == "color")["options"]]

    assert options and set(options) <= set(T.colors)
    for name in options:
        token = "--color-" + name.replace("_", "-")
        if name == "gold":  # the default rule
            assert "color: var(--color-gold);" in text
            continue
        rule = re.search(rf'\.art\[data-color="{name}"\] \{{([^}}]*)\}}', text).group(1)
        assert f"color: var({token});" in rule
    # no colour is written as a literal: the linter checks it, this names the contract
    assert not re.search(r"#[0-9A-Fa-f]{3,6}\b", code_of(html()).replace("#graphic-root", ""))


def test_a_graphic_sits_against_a_corner_of_the_safe_zone_or_in_the_middle_and_in_one_of_three_sizes():
    text = html()

    for corner, sides in {
        "top-left": ("left", "top"),
        "top-right": ("right", "top"),
        "bottom-left": ("left", "bottom"),
        "bottom-right": ("right", "bottom"),
    }.items():
        rule = re.search(rf'\.place\[data-position="{corner}"\] \{{([^}}]*)\}}', text).group(1)
        assert all(f"{side}: var(--safe-{side});" in rule for side in sides), corner
    centre = re.search(r'\.place\[data-position="center"\] \{([^}]*)\}', text).group(1)
    assert "50%" in centre and "var(--box) / 2" in centre
    for size, pixels in (("small", 160), ("medium", 280), ("large", 440)):
        assert f'.place[data-size="{size}"] {{\n        --box: {pixels}px;' in text
    # the largest fits the safe zone's height with room to spare
    assert 440 < 1080 - T.safe["top"] - T.safe["bottom"]


def test_the_file_is_read_by_a_project_path_and_cleaned_again_as_it_is_loaded():
    text = code_of(html())

    assert "fetch(file)" in text
    assert "http" not in text.replace("https?", "")  # no address anywhere
    assert "lineargradient" in text and "clippath" in text and "foreignobject" not in text
    assert "removeAttribute(attr.name)" in text and "scopeIds(svg)" in text


# -- the script, run in node ------------------------------------------------------------------------

HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const gsap = require(input.gsap).gsap;

class Node {
  constructor(tag, attrs) {
    this.localName = tag;
    this.attributes = Object.entries(attrs || {}).map(([name, value]) => ({ name, value: String(value) }));
    this.children = [];
    this.parentNode = null;
    this.opacity = 1; this.scale = 1; this.rotation = 0; this.strokeDashoffset = 0; this.clipPath = "";
  }
  getAttribute(name) { const a = this.attributes.find((x) => x.name === name); return a ? a.value : null; }
  setAttribute(name, value) {
    const a = this.attributes.find((x) => x.name === name);
    if (a) a.value = String(value); else this.attributes.push({ name, value: String(value) });
  }
  removeAttribute(name) { this.attributes = this.attributes.filter((x) => x.name !== name); }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter((c) => c !== child); child.parentNode = null; return child; }
  descendants() { return this.children.flatMap((c) => [c, ...c.descendants()]); }
  querySelectorAll(selector) {
    const all = this.descendants();
    if (selector === "*") return all;
    if (selector === "[id]") return all.filter((n) => n.getAttribute("id") !== null);
    const tags = selector.split(",");
    return all.filter((n) => tags.includes(n.localName));
  }
  querySelector(selector) { return stage[selector] || null; }
  getElementsByTagName() { return []; }
}
const stage = {
  "#graphic-root": new Node("div", { "data-width": "1920" }),
  ".place": new Node("div", { "data-position": "top-left", "data-size": "medium" }),
  ".art": new Node("div", { "data-color": "gold" }),
};
stage[".place"].appendChild(stage[".art"]);
stage["#graphic-root"].appendChild(stage[".place"]);

function parseXml(text) {
  const re = /<(\/?)([A-Za-z][\w:-]*)((?:\s+[\w:.-]+\s*=\s*"[^"]*")*)\s*(\/?)>/g;
  const stack = [];
  let top = null, match;
  while ((match = re.exec(text))) {
    const [, closing, tag, attrText, selfClosing] = match;
    if (closing) { stack.pop(); continue; }
    const attrs = {};
    for (const a of attrText.matchAll(/([\w:.-]+)\s*=\s*"([^"]*)"/g)) attrs[a[1]] = a[2];
    const node = new Node(tag, attrs);
    if (stack.length) stack[stack.length - 1].appendChild(node); else top = top || node;
    if (!selfClosing) stack.push(node);
  }
  return top;
}
class DOMParser { parseFromString(text) { return { documentElement: parseXml(text), getElementsByTagName: () => [] }; } }

const html = new Node("html", input.declaration ? { "data-composition-variables": input.declaration } : {});
const document = { documentElement: html, querySelector: (s) => stage[s] || null };
const getComputedStyle = () => ({ getPropertyValue: (name) => input.css[name] || "" });
const fetched = [];
const fetch = (url) => {
  fetched.push(url);
  const body = input.files[url];
  return Promise.resolve(
    body === undefined
      ? { ok: false, status: 404 }
      : { ok: true, status: 200, text: () => Promise.resolve(body) },
  );
};
const ctx = { gsap, document, getComputedStyle, fetch, DOMParser, console };
ctx.window = ctx;
ctx.__hyperframes = { getVariables: () => input.variables };
vm.createContext(ctx);

const out = { error: null, result: null };
process.on("unhandledRejection", (err) => {
  out.error = String(err && err.message ? err.message : err);
  console.log(JSON.stringify(out));
  process.exit(0);
});
(async () => {
  try {
    vm.runInContext(input.code, ctx);
    for (let i = 0; i < 100 && !(ctx.__timelines && ctx.__timelines[input.id]); i++) {
      await new Promise((resolve) => setTimeout(resolve, 2));
    }
    const tl = ctx.__timelines && ctx.__timelines[input.id];
    if (!tl) throw new Error("no timeline registered as " + input.id);
    const place = stage[".place"], art = stage[".art"];
    const svg = art.children[0];
    const shapes = svg ? svg.descendants() : [];
    out.result = (function (tl, place, art, svg, shapes, fetched, ctx, stage) { %(after)s })(tl, place, art, svg, shapes, fetched, ctx, stage);
  } catch (err) {
    out.error = String(err && err.message ? err.message : err);
  }
  console.log(JSON.stringify(out));
})();
"""

REPORT = r"""
const state = (n) => ({ opacity: n.opacity, scale: n.scale, rotation: n.rotation, dash: n.strokeDashoffset, clip: n.clipPath });
const at = (t) => { tl.time(t); return { place: state(place), art: state(art), shapes: shapes.map(state) }; };
const kids = tl.getChildren(false, true, false);
const starts = kids.map((k) => k.startTime());
return {
  duration: tl.duration(), tweens: kids.length, starts,
  first: at(0), entered: at(%(enter)r), hold: at(tl.duration() / 2), end: at(tl.duration()),
  svg: svg ? svg.attributes.reduce((o, a) => ({ ...o, [a.name]: a.value }), {}) : null,
  shapeNames: shapes.map((s) => s.localName), fetched,
  place: Object.fromEntries(place.attributes.map((a) => [a.name, a.value])),
  art: Object.fromEntries(art.attributes.map((a) => [a.name, a.value])),
  dashes: shapes.map((s) => [s.getAttribute("pathLength"), s.getAttribute("stroke-dasharray")]),
};
"""


def run(
    variables: dict[str, Any] | None = None,
    files: dict[str, str] | None = None,
    after: str | None = None,
    mounted: bool = False,
) -> dict[str, Any]:
    """Run the block's script; ``{"error": ..., "result": ...}``. ``files`` is what ``fetch`` finds, by path."""
    text = html()
    scripts = re.findall(r"<script>(.*?)</script>", text, re.DOTALL)
    default_file = defaults()["svg"]
    payload = {
        "gsap": str(GSAP),
        "code": scripts[-1],
        "declaration": None if mounted else json.dumps(declared()),
        "variables": (defaults() if mounted else {}) | (variables or {}),
        "css": css_properties(),
        "id": "graphic-stamp",
        "files": {default_file: STROKED} | (files or {}),
    }
    program = after or REPORT % {"enter": T.duration["enter"]}
    harness = HARNESS % {"after": program}
    return json.loads(run_node(harness, payload).splitlines()[-1])


def reported(variables: dict[str, Any] | None = None, svg: str = STROKED) -> dict[str, Any]:
    file = (variables or {}).get("svg", defaults()["svg"])
    out = run(variables, {file: svg})
    assert out["error"] is None, out
    return out["result"]


@needs_node
def test_a_stamp_scales_down_and_turns_onto_the_frame_and_the_fade_out_ends_with_the_slot():
    for seconds in (2.4, 4, 9):
        shot = reported({"durationSeconds": seconds, "animation": "stamp"})

        assert shot["duration"] == pytest.approx(seconds, abs=1e-9)
        assert shot["tweens"] == 2
        assert shot["first"]["art"] == {
            "opacity": 0,
            "scale": 1.3,
            "rotation": -6,
            "dash": 0,
            "clip": "",
        }
        assert shot["entered"]["art"]["opacity"] == 1
        assert shot["entered"]["art"]["scale"] == pytest.approx(1, abs=1e-9)
        assert shot["entered"]["art"]["rotation"] == pytest.approx(0, abs=1e-9)
        assert shot["hold"]["place"]["opacity"] == 1 and shot["end"]["place"]["opacity"] == 0
        assert shot["starts"] == pytest.approx([0, seconds - T.duration["exit"]])


@needs_node
def test_a_fade_changes_opacity_only():
    shot = reported({"animation": "fade"})

    assert shot["first"]["art"]["opacity"] == 0 and shot["entered"]["art"]["opacity"] == 1
    assert shot["first"]["art"]["scale"] == 1 and shot["first"]["art"]["rotation"] == 0


@needs_node
def test_a_draw_on_draws_every_stroke_one_micro_step_apart_at_most_and_all_end_with_the_enter_time():
    shot = reported({"animation": "draw-on", "durationSeconds": 4})

    assert shot["tweens"] == 4  # three strokes and the fade out
    assert shot["dashes"] == [["1", "1 2"]] * 3  # pathLength 1, a dash of one and a gap of two
    assert shot["first"]["art"]["opacity"] == 1  # the strokes are what is hidden, not the art
    assert [s["dash"] for s in shot["first"]["shapes"]] == [1.02] * 3
    assert [s["dash"] for s in shot["entered"]["shapes"]] == [0, 0, 0]
    stroke_starts = sorted(shot["starts"])[:3]
    assert stroke_starts[0] == 0 and stroke_starts[-1] == pytest.approx(T.duration["micro"])
    assert stroke_starts[-1] - stroke_starts[0] <= T.duration["group_stagger_total_max"]
    # a stroke at the last start has the rest of the enter time to draw in: they end together
    drawn_by = [s + (T.duration["enter"] - T.duration["micro"]) for s in stroke_starts]
    assert drawn_by[-1] == pytest.approx(T.duration["enter"])


@needs_node
def test_a_draw_on_of_a_filled_shape_wipes_it_in_from_the_left():
    shot = reported({"animation": "draw-on"}, svg=FILLED)

    assert shot["tweens"] == 2
    assert shot["first"]["art"]["clip"].startswith("inset(-4% 100%")
    assert shot["entered"]["art"]["clip"].startswith("inset(-4% -4%")
    assert shot["dashes"] == [[None, None]]


@needs_node
def test_the_picture_aligns_to_the_corner_it_is_placed_in_and_the_options_reach_the_page():
    expect = {
        "top-left": "xMinYMin",
        "top-right": "xMaxYMin",
        "bottom-left": "xMinYMax",
        "bottom-right": "xMaxYMax",
        "center": "xMidYMid",
    }
    for position, align in expect.items():
        shot = reported({"position": position, "size": "large", "color": "oxblood"})

        assert shot["svg"]["preserveAspectRatio"] == f"{align} meet"
        assert shot["place"]["data-position"] == position and shot["place"]["data-size"] == "large"
        assert shot["art"]["data-color"] == "oxblood"
    assert "width" not in shot["svg"] and "height" not in shot["svg"]


@needs_node
def test_it_fetches_the_path_it_was_given_and_only_that():
    shot = reported({"svg": "assets/media/vectors/arrow.svg"})

    assert shot["fetched"] == ["assets/media/vectors/arrow.svg"]


@needs_node
def test_a_file_that_is_hostile_is_cleaned_as_it_loads():
    hostile = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" onload="x()" style="fill:red">'
        '<script>alert(1)</script><foreignObject><div/></foreignObject><image href="data:image/png;base64,AAAA"/>'
        '<a href="https://example.com"><path d="M0 0" onclick="x()" style="fill:red" fill="url(https://e.com/a#g)"/></a>'
        '<use href="https://example.com/x.svg#a" xlink:href="javascript:alert(1)"/>'
        '<path d="M1 1" fill="currentColor"/></svg>'
    )
    out = run(
        {"animation": "fade"},
        after=(
            "const names = art.children[0].descendants().map((n) => n.localName);"
            "const attrs = [art.children[0], ...art.children[0].descendants()].flatMap((n) => n.attributes.map((a) => a.name));"
            "return { names, attrs };"
        ),
        files={defaults()["svg"]: hostile},
    )

    assert out["error"] is None, out
    names, attrs = out["result"]["names"], out["result"]["attrs"]
    assert names == ["use", "path"], names  # the <a> took its path with it
    assert not {"script", "foreignobject", "image", "a"} & {n.lower() for n in names}
    assert not any(a.startswith("on") or a == "style" for a in attrs)
    assert "href" not in attrs and "xlink:href" not in attrs
    assert "fill" in attrs  # the allowed one stays


@needs_node
def test_every_id_is_renumbered_with_its_references_so_two_graphics_never_share_a_gradient():
    gradient = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<defs><linearGradient id="g"><stop offset="0" stop-color="#E6B94D"/></linearGradient>'
        '<path id="p" d="M0 0h4v4z"/></defs>'
        '<rect width="9" height="9" fill="url(#g)"/><use href="#p"/></svg>'
    )
    out = run(
        {"animation": "fade"},
        after=(
            "const all = art.children[0].descendants();"
            "const attr = (n, name) => n.getAttribute(name);"
            "return { ids: all.filter((n) => attr(n, 'id')).map((n) => attr(n, 'id')),"
            " fill: attr(all.find((n) => n.localName === 'rect'), 'fill'),"
            " use: attr(all.find((n) => n.localName === 'use'), 'href') };"
        ),
        files={defaults()["svg"]: gradient},
    )

    assert out["error"] is None, out
    assert out["result"] == {
        "ids": ["gs1-g", "gs1-p"],
        "fill": "url(#gs1-g)",
        "use": "#gs1-p",
    }


@needs_node
def test_a_vector_inside_a_defs_or_a_clip_is_not_drawn_on():
    clipped = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" stroke="currentColor">'
        '<defs><clipPath id="c"><rect width="5" height="5"/></clipPath></defs><path d="M0 0h5" clip-path="url(#c)"/></svg>'
    )
    shot = reported({"animation": "draw-on"}, svg=clipped)

    # defs, clipPath, the rect in the clip, then the path: only the last draws a stroke
    assert shot["dashes"] == [[None, None]] * 3 + [["1", "1 2"]]


@needs_node
def test_a_wrong_variable_fails_at_once_and_a_bad_file_fails_the_render():
    bad = {
        "../x.svg": ({"svg": "assets/../x.svg"}, "svg must be a path"),
        "address": ({"svg": "https://example.com/x.svg"}, "svg must be a path"),
        "absolute": ({"svg": "/etc/x.svg"}, "svg must be a path"),
        "not svg": ({"svg": "assets/media/x.png"}, "svg must be a path"),
        "position": ({"position": "middle"}, "position must be"),
        "size": ({"size": "huge"}, "size must be"),
        "colour": ({"color": "red"}, "color must be"),
        "animation": ({"animation": "spin"}, "animation must be"),
        "short": ({"durationSeconds": 2.3}, "is shorter than the 2.40 s it needs"),
    }
    for name, (variables, message) in bad.items():
        out = run(variables, after="return null;")

        assert out["error"] and message in out["error"], (name, out)
    assert "answered 404" in run({"svg": "assets/media/missing.svg"}, files={})["error"]
    assert "is not an SVG file" in run(files={defaults()["svg"]: "just words"})["error"]


@needs_node
def test_the_minimum_slot_is_accepted_and_works_mounted_where_the_html_element_is_not_in_the_page():
    shot = reported({"durationSeconds": 2.4})
    mounted = run({"position": "bottom-right", "animation": "fade"}, mounted=True)

    assert shot["duration"] == pytest.approx(2.4)
    assert mounted["error"] is None and mounted["result"]["duration"] == 4
    assert mounted["result"]["place"]["data-position"] == "bottom-right"


# -- a shot list may use the block too --------------------------------------------------------------


def test_a_shot_list_names_the_vector_in_its_assets_folder_and_the_composer_copies_it_into_the_project(
    tmp_path,
):
    assets = tmp_path / "assets"
    (assets / "vectors").mkdir(parents=True)
    (assets / "vectors" / "seal.svg").write_text(STROKED)
    (assets / "voice.wav").write_bytes(b"voice")
    layer = {
        "component": "graphic-stamp",
        "at": 0.5,
        "variables": {
            "svg": "vectors/seal.svg",
            "position": "center",
            "size": "small",
            "color": "gold",
            "animation": "draw-on",
        },
    }
    raw = {
        "version": 1,
        "meta": {"title": "A seal", "language": "en"},
        "audio": {"voice": {"path": "voice.wav"}},
        "shots": [{"id": "s1", "start": 0, "duration": 6, "layers": [layer]}],
    }

    done = compose(parse_shotlist(raw), [], 6.0, tmp_path / "p", assets_dir=assets)

    page = (tmp_path / "p" / "index.html").read_text(encoding="utf-8")
    assert (tmp_path / "p/assets/media/vectors/seal.svg").read_text() == STROKED
    assert "assets/media/vectors/seal.svg" in done.files
    assert "&quot;svg&quot;: &quot;assets/media/vectors/seal.svg&quot;" in page
    assert 'data-timeline-label="s1 · Graphic: seal"' in page
    assert (tmp_path / "p/compositions/graphic-stamp.html").is_file()
