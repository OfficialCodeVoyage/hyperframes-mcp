"""The tools that change a project's page: ``replace_media``, ``edit_timeline``, ``set_variable``, ``add_block``,
``arrange_layer`` and ``undo``.

Each edit is one history entry of ours (``hyperframes-mcp``) in the HyperFrames app's log, so the person undoes it alone: see
``edits.py`` for the two routes, the app's (a version-checked ``file.write``) and the disk's (the CLI and an atomic write).
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from hyperframes_mcp.motion import composing, project_edit, timeline_edit
from hyperframes_mcp.motion.project_edit import EditError
from hyperframes_mcp.server import edits
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import ProjectError, resolve_project

EDIT = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
MAX_UNDO = 10


def _seconds(times: list[float]) -> str:
    """Times for a hint: ``1.5 s`` or ``1.5, 4 s``."""
    return ", ".join(f"{t:g}" for t in times) + " s"


def register(server: MCPServer, bridge: AppBridge) -> None:
    """Add the edit tools to ``server``; they reach the HyperFrames app through ``bridge``."""

    @server.tool(annotations=EDIT)
    @reported
    async def replace_media(
        layer: str,
        file: str,
        credit: str | None = None,
        credit_layer: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Put a picture or clip (``file``) in the place of one photo-shot's or video-shot's layer (``layer``, e.g.
        l-vault-1) and set the credit line on screen with it. A file made by confirm_spend or fetched by fetch_media
        brings its own credit (its record); any other needs ``credit`` (who made it, shown on screen). ``credit_layer`` picks the credit line when more
        than one is on screen with the layer. ``file`` must be in the project; it is copied into assets/media/edits/
        unless it is there already. One history entry of yours; undo takes it back.
        """
        # only the project folder: the manifest's assets folder is not trusted, as the agent can rewrite the manifest
        root, _ = resolve_project(project)
        source = (root / file).resolve()
        if not source.is_relative_to(root):
            raise ProjectError(f"{file} is outside the project; copy it into the project first")
        is_clip = file.lower().endswith(tuple(project_edit.MEDIA["video-shot"][1]))
        probe = None
        if is_clip:
            probe = composing.clip_probe([])
        async with edits.turn(bridge, root, f"Replace the media of {layer}") as turn:
            done = await turn.edit(
                lambda page: project_edit.replace_media_in(
                    root,
                    page,
                    layer,
                    source,
                    credit=credit,
                    credit_layer=credit_layer,
                    clip_seconds=probe,
                )
            )
        return {
            "project": str(root),
            **dataclasses.asdict(done),
            **edits.recorded(turn),
        }

    @server.tool(annotations=EDIT)
    @reported
    async def edit_timeline(
        ops: list[dict[str, Any]],
        dry_run: bool = True,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Move, trim, split, duplicate or delete layers in one batch, through HyperFrames' own timeline edit. ``ops`` are
        {"verb": "move", "layer": "l-vault-1", "time": 12.5} (time: seconds, "+2"/"-2" from its start, or
        "after:l-vault-2"), {"verb": "trim", "layer": ..., "start"/"end"/"duration": ...}, {"verb": "split", "layer": ...,
        "time": ...}, {"verb": "duplicate", "layer": ..., "at": ...} and {"verb": "delete", "layer": ...}, layers by
        the ids layer_table gives. ``dry_run`` (the default) writes nothing and says what each layer would become;
        look at it, then call again with dry_run=false. A duplicate pushes the later layers of its track on (they are
        listed as pushed). A split or duplicate makes a layer under the next free id of its shot. A trimmed layer's
        block plays inside its new length. One history entry of yours; undo takes it back.
        """
        root, _ = resolve_project(project)
        planned = await asyncio.to_thread(timeline_edit.plan, root, ops)
        said = {"project": str(root), "dry_run": dry_run, "applied": not dry_run}
        if dry_run:
            return {
                **said,
                "changes": planned.summary.changes,
                "warnings": planned.summary.warnings,
                "next": "NOT applied: nothing was written. Call again with the same ops and dry_run=false to apply them",
            }
        label = "Timeline: " + ", ".join(f"{op['verb']} {op['layer']}" for op in ops)
        async with edits.turn(bridge, root, label) as turn:
            summary = await asyncio.to_thread(timeline_edit.apply, root, planned)
            try:
                await turn.edit(lambda page: (timeline_edit.finish_page(page, summary), None))
            except EditError as error:
                raise EditError(
                    f"{error}; the timeline edit itself was written under its CLI ids: call undo to take it back"
                ) from error
        return {
            **said,
            **edits.recorded(turn),
            "changes": summary.changes,
            "warnings": summary.warnings,
            "next": f"call run_checks, then look with get_frame at {_seconds(summary.check_at())}; undo takes this edit back",
        }

    @server.tool(annotations=EDIT)
    @reported
    async def set_variable(
        layer: str,
        name: str,
        value: str | float | bool,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Change one variable of one layer's block: its text, a number, a colour, a position or another option
        (block_info lists each block's variables with their types and limits). The value is checked against the
        block's own variable; a name or value it does not take is refused, as is a change that would leave a
        picture without a credit line. The layer's length is set with edit_timeline and its file with replace_media.
        One history entry of yours; undo takes it back.
        """
        root, _ = resolve_project(project)
        # refused before any history entry is opened
        page = (root / "index.html").read_text(encoding="utf-8")
        project_edit.set_variable_in(page, layer, name, value)
        async with edits.turn(bridge, root, f"Set {name} of {layer}") as turn:
            done = await turn.edit(lambda page: project_edit.set_variable_in(page, layer, name, value))
        return {
            "project": str(root),
            **dataclasses.asdict(done),
            **edits.recorded(turn),
        }

    @server.tool(annotations=EDIT)
    @reported
    async def add_block(
        component: str,
        start: float,
        duration: float,
        values: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Add one more layer to the video: any block that needs no file (counter, ledger, bar-grid, compare-bars, split-bar,
        act-title, quote-card, credit-line, date-stamp, lower-third; a vector graphic is add_graphic, a picture or clip
        is replace_media). It shows from ``start`` for ``duration`` seconds (block_info gives each block's limits).
        ``values`` must give every text and number variable (block_info names them, with types and limits): a block's
        default is a sample and never goes on screen, so write the value, an empty string when it is not used; the
        others (colours, options) take the block's defaults. The layer joins the shot on screen at ``start`` under the next id of that shot (l-<shot>-<n>) and is
        put in front of every layer it shares time with; the answer warns when its text is too long for the time given
        or when it now hides another layer. Place it with the narration in mind (narration gives word times), then look
        at it with get_frame and run_checks. One history entry of yours; undo takes it back.
        """
        root, _ = resolve_project(project)
        # refused before any history entry is opened, and with nothing installed
        project_edit.add_block_in(
            root,
            (root / "index.html").read_text(encoding="utf-8"),
            component,
            start,
            duration,
            values,
            install=False,
        )
        async with edits.turn(bridge, root, f"Add {component} at {start:g} s") as turn:
            done = await turn.edit(
                lambda page: project_edit.add_block_in(root, page, component, start, duration, values)
            )
        return {
            "project": str(root),
            **dataclasses.asdict(done),
            **edits.recorded(turn),
            "next": f"look at it with get_frame at {min(done.start + 2, done.end - 0.5):g} s, then run_checks; undo takes it back",
        }

    @server.tool(annotations=EDIT)
    @reported
    async def arrange_layer(
        layer: str,
        dx: float | None = None,
        dy: float | None = None,
        scale: float | None = None,
        reset: bool = False,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Move or resize one layer (a counter, ledger, chart, title, date stamp, lower third, graphic or credit line; not
        a picture or clip, which fill the frame). ``dx`` and ``dy`` are pixels of the 1920x1080 canvas, right and down, from
        where the block puts itself (-100 moves it 100 px left); ``scale`` is a factor around the frame's centre (0.2 to
        2, so 0.8 makes it smaller and a little more central). What you leave out stays as it is, and ``reset`` puts the
        layer back where its block puts it. Nothing knows where a block draws its words, so look at it with get_frame at
        a time it is on screen (also where a credit line or date stamp shows with it) and nudge again. One history
        entry of yours; undo takes it back.
        """
        root, _ = resolve_project(project)
        # refused before any history entry is opened
        project_edit.arrange_layer_in(
            (root / "index.html").read_text(encoding="utf-8"),
            layer,
            dx=dx,
            dy=dy,
            scale=scale,
            reset=reset,
        )
        async with edits.turn(bridge, root, f"Move {layer}") as turn:
            done = await turn.edit(
                lambda page: project_edit.arrange_layer_in(page, layer, dx=dx, dy=dy, scale=scale, reset=reset)
            )
        return {
            "project": str(root),
            **dataclasses.asdict(done),
            **edits.recorded(turn),
            "next": "look at it with get_frame while it is on screen; undo takes this move back",
        }

    @server.tool(annotations=EDIT)
    @reported
    async def undo(steps: int = 1, project: str | None = None) -> dict[str, Any]:
        """Take back your own last ``steps`` edits (the history entries of hyperframes-mcp), newest first. It never touches
        the person's: when they changed a file you edited after you did, it stops and says so. Says which entries it
        undid and how many of yours are left.
        """
        if not 1 <= steps <= MAX_UNDO:
            raise EditError(f"steps is 1 to {MAX_UNDO}, not {steps}")
        root, _ = resolve_project(project)
        return {"project": str(root), **await edits.undo_ours(bridge, root, steps)}
