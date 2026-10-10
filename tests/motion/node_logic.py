"""Run the pure logic of a motion block in Node, with the GSAP that `hyperframes-mcp setup` installs.

A block keeps what is arithmetic (number formatting, the time plan, the layout fit) in functions between a
``// logic:begin`` and a ``// logic:end`` line of its script, with no DOM in them. A test extracts that text, runs it
in Node after ``gsap.min.js`` from the setup install is loaded, and reads back one JSON value, so the code that is tested is the
code that renders. A JavaScript ``throw`` comes back as :class:`LogicError`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from hyperframes_mcp.motion import tokens
from hyperframes_mcp.util.paths import node_dir

NODE = shutil.which("node") or "/opt/homebrew/bin/node"
GSAP = node_dir() / "node_modules" / "gsap" / "dist" / "gsap.min.js"

needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not installed")


class LogicError(Exception):
    """The block's logic threw; the message is the JavaScript error message."""


def logic_of(block: Path) -> str:
    """The text between the ``// logic:begin`` and ``// logic:end`` lines of a block."""
    text = block.read_text(encoding="utf-8")
    begin = text.index("// logic:begin")
    end = text.index("// logic:end")
    return text[begin:end]


def run(block: Path, program: str) -> Any:
    """Run ``program`` after the block's logic and return what it passes to ``emit(value)``.

    ``gsap`` is a global, as in the page; ``program`` may use every function of the logic.
    """
    script = (
        # the page runs the logic under "use strict": an undeclared assignment must fail here too
        '"use strict";\n'
        f"globalThis.gsap = require({json.dumps(str(GSAP))}).gsap;\n"
        "let __out;\n"
        "const emit = (value) => { __out = value; };\n"
        f"{logic_of(block)}\n"
        "try {\n"
        f"{program}\n"
        "  process.stdout.write(JSON.stringify({ok: __out}));\n"
        "} catch (error) {\n"
        "  process.stdout.write(JSON.stringify({error: String(error.message)}));\n"
        "}\n"
    )
    done = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "error" in result:
        raise LogicError(result["error"])
    return result["ok"]


def call(block: Path, name: str, *args: Any) -> Any:
    """Call one function of the block's logic with JSON arguments and return its JSON result."""
    arguments = ", ".join(json.dumps(a) for a in args)
    return run(block, f"emit({name}({arguments}));")


@cache
def newsreader_advances(weight: int, optical_size: int) -> dict[str, float]:
    """Advance of every character of Newsreader, in em, at one weight and optical size.

    This is what the browser draws: it sets the optical-size axis from the font size (72 at a heading of 72 px or
    more) and the weight from ``font-weight``. The advances in the default instance of the file (digits 0.55 em)
    are narrower than a figure set at weight 500, and are not the ones to size text by.
    """
    pytest.importorskip("fontTools.varLib.instancer")
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = instancer.instantiateVariableFont(
        TTFont(tokens.FONTS_DIR / "Newsreader-Variable.ttf"),
        {"wght": weight, "opsz": optical_size},
    )
    upem = font["head"].unitsPerEm
    hmtx = font["hmtx"]
    return {chr(code): hmtx[name][0] / upem for code, name in font.getBestCmap().items()}
