"""Shared by the map tests: paths, the block's text and declarations, the shipped map data, boxes, and a way to call map-lib.js under node."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import cache

import pytest

from hyperframes_mcp.motion import maps, registry, tokens

MOTION = tokens.MOTION_DIR
BLOCK = MOTION / "blocks" / "map-route.html"
LIB = MOTION / "js" / "map-lib.js"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")
T = tokens.default_tokens()
SAFE = {
    "x0": T.safe["left"],
    "y0": T.safe["top"],
    "x1": T.canvas["width"] - T.safe["right"],
    "y1": T.canvas["height"] - T.safe["bottom"],
}


def html() -> str:
    return BLOCK.read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The file without its leading licence comment."""
    return text.split("-->", 1)[1]


def declared() -> list[dict]:
    """The variables of the block (the registry lists what the HTML declares; test_registry compares the two)."""
    return list(registry.get("map-route").variables)


def defaults() -> dict:
    return {v["id"]: v["default"] for v in declared()}


def enum_options() -> dict[str, list]:
    """The allowed values of each enum variable of the block."""
    return {v["id"]: [o["value"] for o in v["options"]] for v in declared() if v["type"] == "enum"}


@cache
def load_script(name: str) -> dict:
    """The object a shipped map-NAME.js registers (the file is a one-line JSON assignment after its header)."""
    text = (maps.DATA_DIR / f"map-{name}.js").read_text(encoding="utf-8")
    body = re.search(r'window\.__MOTION_MAPS\["[a-z]+"\] = (\{.*\});\n$', text, re.DOTALL)
    assert body, f"map-{name}.js is not a registration of window.__MOTION_MAPS"
    return json.loads(body.group(1))


def apart(a: dict, b: dict, pad: float = 0) -> bool:
    """True when two boxes (x0, y0, x1, y1) are at least ``pad`` apart."""
    return a["x0"] >= b["x1"] + pad or a["x1"] <= b["x0"] - pad or a["y0"] >= b["y1"] + pad or a["y1"] <= b["y0"] - pad


def in_safe(box: dict) -> bool:
    """True when a box lies inside the safe zone."""
    return SAFE["x0"] <= box["x0"] and box["x1"] <= SAFE["x1"] and SAFE["y0"] <= box["y0"] and box["y1"] <= SAFE["y1"]


def run_lib(expression: str, payload=None):
    """The value of a JavaScript expression that sees ``lib`` (map-lib.js) and ``input`` (the payload).

    A throw comes back as ``{"error": message}``.
    """
    script = (
        f"const lib = require({json.dumps(str(LIB))});"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        f"let out; try {{ out = ({expression}); }} catch (e) {{ out = {{ error: e.message }}; }}"
        "process.stdout.write(JSON.stringify(out));"
    )
    done = subprocess.run(
        [NODE, "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)
