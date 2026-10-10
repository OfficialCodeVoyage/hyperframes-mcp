"""Run a block's own script in node against the real GSAP and a stand-in page, and look at its timeline.

A static test can read a block; this runs it. The script is the last inline ``<script>`` of the file, executed in a
``vm`` context with ``gsap.min.js`` from the setup install, a ``document`` whose elements are plain objects (GSAP then tweens
their properties as numbers, with no browser), and ``getComputedStyle`` answering from the generated ``tokens.css``.
Variables arrive the way HyperFrames gives them: through ``window.__hyperframes.getVariables()``. With
``mounted=True`` the page is a mounted composition: the ``<html>`` element of the block is not in it (no declaration to
read) and ``getVariables()`` carries the declared defaults plus the overrides, which is what the pinned HyperFrames does.

What it cannot see: layout, fonts, the grading shader, anything drawn. Those are for the rendered frames.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import ImageColor

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.util.paths import node_dir

NODE = shutil.which("node")
GSAP = node_dir() / "node_modules" / "gsap" / "dist" / "gsap.min.js"
needs_node = pytest.mark.skipif(
    NODE is None or not GSAP.is_file(), reason="node or GSAP is not installed (run `hyperframes-mcp setup`)"
)


def read_block(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The file without its leading licence comment."""
    return text.split("-->", 1)[1]


def declared_variables(path: Path) -> list[dict[str, Any]]:
    """The ``data-composition-variables`` of a block, as the list of declarations."""
    raw = re.search(r"data-composition-variables='(\[.*?\])'", read_block(path), re.DOTALL).group(1)
    return json.loads(raw)


def default_variables(path: Path) -> dict[str, Any]:
    return {v["id"]: v["default"] for v in declared_variables(path)}


def over_white(colour: str, alpha: float) -> str:
    """``colour`` at ``alpha`` over pure white as ``#RRGGBB``: the worst frame a text on a plate can sit on."""
    profiles = [c * alpha + 255 * (1 - alpha) for c in ImageColor.getrgb(colour)]
    return "#" + "".join(f"{round(c):02X}" for c in profiles)


# The harness: the block's script runs in a vm context; ``%(after)s`` is the body of a function that gets ``tl`` (the
# registered timeline), ``els`` (selector -> stand-in element or list of them) and ``ctx`` and returns JSON.
HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const gsap = require(input.gsap).gsap;

class El {
  constructor(attrs) {
    this.attrs = Object.assign({}, attrs);
    this.style = {};
    this.textContent = "";
    this.opacity = 1; this.scale = 1; this.scaleX = 1; this.x = 0; this.y = 0; this.clipPath = "";
  }
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  querySelector(selector) { return find(selector); }
  querySelectorAll(selector) { return findAll(selector); }
}
const els = {};
function make(selector, attrs) {
  return Object.assign(new El(attrs), input.initial[selector] || {});
}
function find(selector) {
  if (!els[selector]) els[selector] = make(selector, selector === input.root ? input.rootAttrs : {});
  return els[selector];
}
function findAll(selector) {
  const count = input.counts[selector] || 1;
  const key = "all:" + selector;
  if (!els[key]) els[key] = Array.from({ length: count }, () => make(selector, {}));
  return els[key];
}
const html = new El(input.declaration ? { "data-composition-variables": input.declaration } : {});
const document = { documentElement: html, querySelector: find, querySelectorAll: findAll };
const getComputedStyle = () => ({ getPropertyValue: (name) => input.css[name] || "" });
const ctx = { gsap, document, getComputedStyle, console };
ctx.window = ctx;
ctx.__hyperframes = { getVariables: () => input.variables };
vm.createContext(ctx);
const out = { error: null, result: null };
try {
  vm.runInContext(input.code, ctx);
  const tl = ctx.__timelines && ctx.__timelines[input.id];
  if (!tl) throw new Error("no timeline registered as " + input.id);
  out.result = (function (tl, els, ctx) { %(after)s })(tl, els, ctx);
} catch (err) {
  out.error = String(err && err.message ? err.message : err);
}
console.log(JSON.stringify(out));
"""


def css_properties() -> dict[str, str]:
    """The custom properties ``tokens.css`` defines, as a name to value map."""
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", tokens.css())}


def run_block(
    path: Path,
    variables: dict[str, Any] | None = None,
    *,
    after: str = "return null;",
    root: str,
    counts: dict[str, int] | None = None,
    initial: dict[str, dict[str, Any]] | None = None,
    css: dict[str, str] | None = None,
    mounted: bool = False,
) -> dict[str, Any]:
    """Run the block's script; ``{"error": message or None, "result": what after returned}``.

    ``root`` is the selector of the composition root (``#photo-root``); ``counts`` how many elements a
    ``querySelectorAll`` of a selector finds; ``initial`` the properties the block's CSS gives an element before
    its script runs (``{".tile": {"opacity": 0}}``), which a stand-in page cannot know; ``css`` overrides custom
    properties of ``tokens.css`` (an empty value is a stylesheet that did not load); ``mounted`` runs the block as a
    mounted composition (see the module docstring).
    """
    text = read_block(path)
    declared = declared_variables(path)
    composition_id = re.search(r'data-composition-id="([^"]+)"', text).group(1)
    scripts = re.findall(r"<script>(.*?)</script>", text, re.DOTALL)
    payload = {
        "gsap": str(GSAP),
        "code": scripts[-1],
        "declaration": None if mounted else json.dumps(declared),
        "variables": (default_variables(path) if mounted else {}) | (variables or {}),
        "css": css_properties() | (css or {}),
        "id": composition_id,
        "root": root,
        "rootAttrs": {"data-width": "1920", "data-height": "1080"},
        "counts": counts or {},
        "initial": initial or {},
    }
    return json.loads(run_node(HARNESS % {"after": after}, payload).splitlines()[-1])


def run_node(script: str, payload: Any) -> str:
    """Run a node script that reads one JSON document on stdin; its stdout (the call fails on a non-zero exit)."""
    if NODE is None:
        raise RuntimeError("node is not installed")
    done = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr)
    return done.stdout.strip()
