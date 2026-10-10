"""The vector graphic tools: ``search_icons``, ``save_vector``, ``vectorize`` and ``add_graphic``.

A vector is an SVG: an icon, arrow, seal or stamp that stays sharp, can be recoloured and animated. Three free ways to get
one (``src/hyperframes_mcp/motion/vectors.py``): search the vendored icon sets, write the SVG yourself, or trace a picture of the project
with the optional ``vtracer``. All go through one strict check and are saved under ``assets/media/vectors/`` with a
record; ``add_graphic`` then puts one in the page as a ``graphic-stamp`` layer. The server makes no model call and pays
nothing: the chat's own model writes an SVG, the server only checks it and saves it.
"""

from __future__ import annotations

import dataclasses
import json
import tempfile
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from hyperframes_mcp.motion import frames, icon_sheet, project_edit, vectors
from hyperframes_mcp.server import edits
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import resolve_project

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
# a hit lists this many of its tags: a few names and a licence, never the SVG
SHOWN_TAGS = 8


def register(server: MCPServer, bridge: AppBridge) -> None:
    """Add the vector tools to ``server``; ``add_graphic`` reaches the HyperFrames app through ``bridge``."""

    @server.tool(annotations=READ_ONLY)
    @reported
    def search_icons(query: str, limit: int = 8) -> dict[str, Any]:
        """Find icons in the free sets kept in the repository (Maki CC0, Tabler MIT, Material Symbols Apache-2.0: coins,
        bank, ship, train, factory, anchor, scale, document, seal, chart arrows, map pins, flags, people, tools and so on) by
        name and tags; every word of ``query`` must match. Each hit gives its id, set, tags, licence and file, and an
        image shows them numbered in that order: look at it before you pick, because a name can mislead (Maki's "bank" is
        a banknote). Put one in the project with save_vector, giving its id.
        """
        hits = vectors.search_icons(query, limit)
        found: dict[str, Any] = {
            "query": query,
            "count": len(hits),
            "icons": [
                {
                    "id": icon.id,
                    "name": icon.name,
                    "set": icon.set,
                    "tags": list(icon.tags[:SHOWN_TAGS]),
                    "licence": icon.licence,
                    "path": str(icon.path),
                }
                for icon in hits
            ],
            "next": (
                "look at the image, then save_vector(svg=<an id>, name=...) puts one in the project"
                if hits
                else "no icon matches every word: try fewer or other words (a synonym, a broader thing)"
            ),
        }
        if not hits:
            return found
        # with a picture of the hits the answer is a result of its own, carrying the same structured answer
        try:
            with tempfile.TemporaryDirectory(prefix="hyperframes-mcp-icons-") as folder:
                sheet = frames.jpeg(icon_sheet.icon_sheet(hits, Path(folder)))
        except (icon_sheet.IconSheetError, OSError) as error:
            # the search still stands; only the look at the candidates is lost
            found["next"] = f"no image of the icons ({error}); " + found["next"]
            return found
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(found, indent=2)),
                Image(data=sheet, format="jpeg").to_image_content(),
            ],
            structured_content=found,
        )

    @server.tool(annotations=EDIT)
    @reported
    def save_vector(svg: str, name: str, project: str | None = None) -> dict[str, Any]:
        """Save a vector in the project as assets/media/vectors/<name>.svg. ``svg`` is either the markup you wrote
        (it must start with <svg xmlns="http://www.w3.org/2000/svg" viewBox="...">) or an icon id from search_icons. The
        file is checked first: drawing elements only (no script, style, text, image, filter or link), no reference
        outside the file, a viewBox, at most 100 KB and 1000 elements, and colours from the profile's palette (a refusal
        lists it) or currentColor, which add_graphic recolours: prefer currentColor for a single-colour mark. A refusal
        lists every problem. A name that exists is not overwritten. ``name``: letters, digits, - and _ (up to 64).
        """
        root, _ = resolve_project(project)
        saved = vectors.save_vector(root, svg, name)
        return _saved(root, saved, name)

    @server.tool(annotations=EDIT)
    @reported
    def vectorize(image: str, name: str, colors: str = "mono", project: str | None = None) -> dict[str, Any]:
        """Trace a picture of the project (``image``, a path in the project) into a vector on this machine, free, with
        vtracer, and save it as assets/media/vectors/<name>.svg after the same check as save_vector. ``colors``:
        mono (one shape colour, currentColor, the best for a seal, a silhouette or an engraved mark) or palette (the
        picture's colours snapped to the palette). A simple picture with a plain light ground traces well; a photograph
        gives far too many shapes. If vtracer is not installed the answer says how to install it.
        """
        root, _ = resolve_project(project)
        saved = vectors.vectorize(root, image, name, colors=colors)
        return _saved(root, saved, name)

    @server.tool(annotations=EDIT)
    @reported
    async def add_graphic(
        vector: str,
        start: float,
        duration: float,
        position: str = "top-left",
        size: str = "medium",
        animation: str = "stamp",
        color: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Add one vector graphic to the video as a new layer: ``vector`` is a name saved by save_vector (or a path of the
        project to an SVG), shown from ``start`` for ``duration`` seconds (at least 2.4). ``position``: top-left,
        top-right, bottom-left, bottom-right or center, inside the safe zone (a credit line sits in the bottom corner on its
        side and a date stamp in the corner it names: the answer warns when the graphic shares a corner with one showing
        then, so choose another corner or time; this moves no other layer). ``size``: small, medium or large.
        ``animation``: stamp (scales down onto the frame), draw-on (strokes draw themselves) or fade. ``color``: a palette
        colour for what the SVG paints with currentColor (gold, gold_dim, paper, paper_aged, ink, oxblood, verdigris,
        slate, loss, gain; default gold). The layer joins the shot on screen at ``start`` and is put in front of every
        layer it shares time with. Gives the new layer's id and time; look at it with get_frame, then run_checks. One
        history entry of yours; undo takes it back.
        """
        root, _ = resolve_project(project)
        async with edits.turn(bridge, root, f"Add graphic {vector} at {start:g} s") as turn:
            done = await turn.edit(
                lambda page: project_edit.add_graphic_in(
                    root,
                    page,
                    vector,
                    start,
                    duration,
                    position=position,
                    size=size,
                    animation=animation,
                    color=color,
                )
            )
        return {
            "project": str(root),
            **dataclasses.asdict(done),
            **edits.recorded(turn),
            "next": f"look at it with get_frame at {done.start + 1:g}, then run_checks; undo takes it back",
        }


def _saved(root: Path, saved: vectors.Saved, name: str) -> dict[str, Any]:
    """What a save tool answers: the project, the file's path, source and licence, and the step after."""
    return {
        "project": str(root),
        **dataclasses.asdict(saved),
        "next": f"place it with add_graphic(vector={name!r}, start=..., duration=...)",
    }
