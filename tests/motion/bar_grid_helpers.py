"""What the ``bar-grid`` test files share: the two shots the block was built for, and calls into its logic in Node."""

from __future__ import annotations

import json
from typing import Any

from hyperframes_mcp.motion import preflight, registry, tokens
from tests.motion import block_helpers
from tests.motion.block_runner import css_properties
from tests.motion.node_logic import LogicError, call, run

BLOCK = tokens.MOTION_DIR / "blocks" / "bar-grid.html"
T = tokens.default_tokens()
D = T.duration
SIZES = {
    "title": T.sizes["title"],
    "subtitle": T.sizes["subtitle"],
    "body": T.sizes["body"],
    "minText": T.sizes["min_text"],
}
SAFE_W = T.canvas["width"] - T.safe["left"] - T.safe["right"]
SAFE_H = T.canvas["height"] - T.safe["top"] - T.safe["bottom"]


js = json.dumps


def html() -> str:
    return block_helpers.html(BLOCK)


def declared() -> list[dict]:
    return block_helpers.declared(BLOCK)


def defaults() -> dict:
    return block_helpers.defaults(BLOCK)


def seg(**over: Any) -> dict:
    return {
        "count": 100,
        "label": "found in the partial count",
        "tone": "gold",
        "at": 0.4,
        "seconds": 3.0,
    } | over


def variables(segments: list[dict] | None = None, **over: Any) -> dict:
    """The variables of a shot: the defaults, with the segments written out as the JSON text the variable holds."""
    return defaults() | {"segments": json.dumps(segments or [seg(count=8198)])} | over


# (a) the cold open: 8198 gold bars in one segment, the block's own defaults
COLD_OPEN = variables()
# (b) a Marburg-sized pile for scale: 3600 asked of Portugal and 320 turned over, the rest left as faint cells
FOR_SCALE = variables(
    [
        seg(
            count=3600,
            label="asked of Portugal (about 45 tonnes)",
            tone="ember",
            at=0.6,
            seconds=3.0,
        ),
        seg(
            count=320,
            label="Portugal turned over (about 4 tonnes)",
            tone="gold",
            at=4.6,
            seconds=1.2,
        ),
    ],
    title="Gold bars: what was asked, what came back",
    note="A Marburg-sized pile, for scale",
    durationSeconds=12,
)
# a small grid with two segments, cheap to draw many times in a test
SMALL = variables(
    [
        seg(count=700, tone="ember", at=0.3, seconds=2.0, label="ember"),
        seg(count=90, tone="gold", at=2.6, seconds=1.0, label="gold"),
    ],
    total=1500,
    durationSeconds=8,
)


def error_of(v: dict) -> str | None:
    """What the block says about variables (the message of its throw), or None when it accepts them."""
    try:
        call(BLOCK, "readConfig", v)
    except LogicError as error:
        return str(error)
    return None


TOK = f"const props = {js(css_properties())};const tok = (n) => Number.parseFloat(props[n]);"


def slot(v: dict) -> float:
    """The block's own minimum slot, as the composer's preflight computes it (``minSlot`` run in Node)."""
    (result,) = preflight.minimum_slots([("bar-grid", v)], registry.default_registry())
    assert result.error is None, result.error
    return result.seconds


def check_slot(v: dict, seconds: float) -> str | None:
    """What the page's slot check says about a slot of ``seconds``: its error, or None."""
    program = (
        f"{TOK} const cfg = readConfig({js(v)}); const p = timingInput(tok); p.duration = {seconds!r};"
        "checkSlot(cfg.segments, p); emit(null);"
    )
    try:
        return run(BLOCK, program)
    except LogicError as error:
        return str(error)


ENV = {
    "sizes": SIZES,
    "safeW": SAFE_W,
    "safeH": SAFE_H,
    "safeTop": T.safe["top"],
}


def call_layout(v: dict, **over: Any) -> dict:
    """``layoutFor`` for variables, on the 1920 x 1080 canvas of the tokens (``over`` replaces a field of the environment)."""
    return run(BLOCK, f"emit(layoutFor(readConfig({js(v)}), {js(ENV | over)}));")


def call_measure(text: str, table_name: str, px: float) -> float:
    return run(BLOCK, f"emit(measure({js(text)}, {table_name}, {px}));")


def hand_minimum(segments: list[dict]) -> float:
    """The documented formula: the last fill ends (at plus seconds), the entrance (the outline pulse), the reading time of
    that segment's label (read_min plus read_per_word a word) and the exit. Of two segments that end together the later
    one in the list is the one read last."""
    last = max(reversed(segments), key=lambda s: s["at"] + s["seconds"])
    return (
        last["at"]
        + last["seconds"]
        + D["enter"]
        + D["read_min"]
        + D["read_per_word"] * len(last["label"].split())
        + D["exit"]
    )


# A stand-in canvas context that records what is drawn, and a model built the way the page builds it: the sprites are
# plain strings ("gold3"), the colours one letter each. ``signature`` is a hash of everything the last draw did.
DRAW_PRELUDE = f"""
const ENTER = {D["enter"]};
function recorder() {{
  const r = {{ ops: [], globalAlpha: 1, lineWidth: 1, strokeStyle: null, fillStyle: null }};
  r.clearRect = function () {{ r.ops = []; }};
  r.drawImage = function (sprite, x, y) {{ r.ops.push(["image", sprite, x, y, r.globalAlpha]); }};
  r.beginPath = function () {{ r.path = []; }};
  r.moveTo = function (x, y) {{ r.path.push([x, y]); }};
  r.lineTo = function (x, y) {{ r.path.push([x, y]); }};
  r.closePath = function () {{}};
  r.stroke = function () {{ r.ops.push(["stroke", r.strokeStyle, r.lineWidth, r.globalAlpha, r.path]); }};
  r.fill = function () {{ r.ops.push(["fill", r.fillStyle, r.globalAlpha, r.path]); }};
  return r;
}}
function signature(ops) {{
  let h = 5381;
  const s = JSON.stringify(ops);
  for (let i = 0; i < s.length; i += 1) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return h + ":" + ops.length;
}}
function build(V) {{
  const cfg = readConfig(V);
  const layout = layoutFor(cfg, {js(ENV)});
  const colors = {{ gold: "G", paper: "P", ember: "E", mute: "M" }};
  const sprites = {{}};
  for (const tone of Object.keys(TONE_TOKEN)) {{
    sprites[tone] = [null];
    for (let level = 1; level <= LEVELS; level += 1) sprites[tone].push(tone + level);
  }}
  return {{ cfg: cfg, layout: layout, model: modelFor(cfg, layout, ENTER, colors), sprites: sprites }};
}}
"""
