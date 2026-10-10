"""What the per-block test files of the motion library share: a block's text, its declaration, its worst-case contrast."""

from __future__ import annotations

import json
import re
from pathlib import Path

from hyperframes_mcp.motion import tokens


def html(block: Path) -> str:
    return block.read_text(encoding="utf-8")


def code_of(text: str) -> str:
    """The file without its leading licence comment."""
    return text.split("-->", 1)[1]


def declared(block: Path) -> list[dict]:
    """The variables a block declares in ``data-composition-variables``."""
    return json.loads(re.search(r"data-composition-variables='(\[.*?\])'", html(block), re.DOTALL).group(1))


def defaults(block: Path) -> dict:
    return {v["id"]: v["default"] for v in declared(block)}


def scrim_contrast(text: str, colour: str) -> float:
    """Contrast of a token colour on the block's scrim at its strongest CSS opacity, over pure white.

    Pure white behind the scrim is the worst frame a shot can give; the result is compared with the
    minimum ratio of the tokens.
    """
    t = tokens.default_tokens()
    alpha = max(int(p) for p in re.findall(r"var\(--color-scrim\) (\d+)%", text)) / 100
    scrim = t.colors["scrim"]
    profiles = [int(scrim[i : i + 2], 16) * alpha + 255 * (1 - alpha) for i in (1, 3, 5)]
    over_white = "#" + "".join(f"{round(c):02X}" for c in profiles)
    return tokens.contrast(t.colors[colour], over_white)
