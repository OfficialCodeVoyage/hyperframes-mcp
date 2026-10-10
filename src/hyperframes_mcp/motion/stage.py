"""Stage one block in a scratch HyperFrames project, ready to render and look at.

A block alone is transparent and has no host page, so a review needs a project around it: the block installed
by ``registry.install_item`` (with GSAP, ``tokens.css`` and its fonts), a root ``index.html`` that mounts it
the way the composer will (``data-composition-src`` plus a JSON-escaped ``data-variable-values``), a night
ground under it, and the ``meta.json`` the HyperFrames CLI uses to recognise a project directory. The
project is a pure function of the block, the variables and the tokens, so the same inputs give the same files.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyperframes_mcp.motion import registry, tokens
from hyperframes_mcp.util.atomic import atomic_write_bytes


class StageError(ValueError):
    """The block cannot be staged with these variables; the message says why."""


@dataclass(frozen=True)
class StagedBlock:
    """A staged project: where it is and the lengths the render and the review frames use."""

    project_dir: Path
    start: float
    # the block's slot (its durationSeconds) and the project's length (the start plus the slot)
    duration_s: float
    total_s: float


def _index_html(name: str, variables: dict[str, Any], start: float, duration: float) -> str:
    """The host page: GSAP, a night ground and one mount of ``name``, all at the canvas of the tokens."""
    canvas = tokens.default_tokens().canvas
    width, height, fps = canvas["width"], canvas["height"], canvas["fps"]
    ground = tokens.default_tokens().colors["night"]
    values = html.escape(json.dumps(variables), quote=True)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <script src="vendor/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: {ground}; }}
      #root {{ position: relative; width: {width}px; height: {height}px; overflow: hidden; }}
      #ground {{ position: absolute; left: 0; top: 0; width: {width}px; height: {height}px; background: {ground}; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{start + duration}" data-width="{width}" data-height="{height}" data-fps="{fps}">
      <div id="ground"></div>
      <div
        id="block"
        data-composition-id="{name}"
        data-composition-src="compositions/{name}.html"
        data-start="{start}"
        data-duration="{duration}"
        data-width="{width}"
        data-height="{height}"
        data-track-index="1"
        data-variable-values="{values}"
      ></div>
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines["main"] = tl;
      tl.seek(0);
    </script>
  </body>
</html>
"""


def write_meta(project: Path, project_id: str, name: str) -> None:
    """Write the ``meta.json`` the HyperFrames CLI needs to take a folder for a project."""
    text = json.dumps({"id": project_id, "name": name})
    atomic_write_bytes(project / "meta.json", (text + "\n").encode("utf-8"))


def stage_block(
    name: str,
    variables: dict[str, Any] | None,
    project_dir: str | Path,
    *,
    start: float = 0.0,
) -> StagedBlock:
    """Build the scratch project of block ``name`` in ``project_dir`` and return what was staged.

    ``variables`` are the overrides of the block's declared variables (``None`` or empty: its defaults). The
    block's slot is the ``durationSeconds`` given there or the item's default; the block is mounted at
    ``start`` seconds and the project lasts until its slot ends.

    Raises:
        registry.RegistryError: no such item (the message lists what exists).
        StageError: the item is not a block, a variable is not declared, or the duration is not positive.
    """
    item = registry.get(name)
    if item.type != "hyperframes:block" or item.duration is None:
        raise StageError(f"{name!r} is a component, not a block: it has nothing to render")
    values = dict(variables or {})
    declared = [variable["id"] for variable in item.variables]
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise StageError(f"{name} has no variable {', '.join(unknown)}; it declares {', '.join(declared)}")
    duration = values.get("durationSeconds", item.duration)
    if not tokens.is_number(duration) or duration <= 0:
        raise StageError(f"durationSeconds must be a positive number, not {duration!r}")
    if not tokens.is_number(start) or start < 0:
        raise StageError(f"start must be zero or more seconds, not {start!r}")
    start, duration = float(start), float(duration)

    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)
    registry.install_item(name, project)
    atomic_write_bytes(
        project / "index.html",
        _index_html(name, values, start, duration).encode("utf-8"),
    )
    write_meta(project, f"motion-{name}", name)
    return StagedBlock(
        project_dir=project,
        start=start,
        duration_s=duration,
        total_s=start + duration,
    )
