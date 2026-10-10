"""The paid tools: quote a picture or a clip, confirm a quote, and see what is spent and what the style is.

Nothing is paid without a quote and its confirmation, and ``hyperframes_mcp.motion.spend`` refuses a confirmation above the limits
(``config/limits.yaml``: a call, a video). The HyperFrames app runs its agent without permission prompts, so the
limits are the gate; the instructions also ask the agent to tell the person what it will make and its price first.
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from hyperframes_mcp.motion import edit_config, generate, spend
from hyperframes_mcp.motion.project_edit import EDITS_DIR
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import ProjectError, resolve_project

QUOTE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
PAID = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
LOOK = (
    "look at the attached preview and tell the person, before the picture goes in a layer, whether it matches the "
    "profile's look (its house rules, in project_info and style_info); if it does not, say so and offer to remake it, "
    "which costs again, so ask first"
)


def _out(name: str, suffix: str) -> str:
    """Where a new file named ``name`` goes in the project: its edits folder."""
    if not _NAME.fullmatch(name):
        raise ProjectError(f"name {name!r}: use letters, digits, - and _ only (up to 64), no folder or suffix")
    return f"{EDITS_DIR}/{name}{suffix}"


def _quote(quote: spend.Quote, root) -> dict[str, Any]:
    return {
        "project": str(root),
        "quote_id": quote.id,
        "kind": quote.kind,
        "price_usd": quote.price_usd,
        "expires_at": quote.expires_at.isoformat(timespec="seconds"),
        "out": quote.arguments["out"],
        **({"prompt": quote.prompt} if quote.prompt else {}),
        **({"clip_cap_left": quote.clip_cap_left} if quote.clip_cap_left else {}),
        "spend": spend.spend_state(root),
        "next": "tell the person what you will make and its price, then call confirm_spend with this quote_id",
    }


def _clip_lengths() -> str:
    """The lengths the clip model makes, for the tool's description: ``4, 6 or 8`` (``a length it makes`` when the model
    cannot be read: the call itself still checks)."""
    try:
        lengths = [str(n) for n in generate.allowed_clip_seconds()]
        return ", ".join(lengths[:-1]) + f" or {lengths[-1]}" if len(lengths) > 1 else lengths[0]
    except Exception:  # noqa: BLE001 -- description text only: whatever is wrong with the settings must not stop the server
        return "a length the model makes"


def register(server: MCPServer) -> None:
    """Add the paid tools to ``server``."""

    @server.tool(annotations=QUOTE)
    @reported
    def quote_picture(
        request: str,
        name: str,
        quality: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Quote one new picture of ``request`` in the profile's style (nothing is paid): its price and the full
        prompt it would send. Describe only the subject, place, era and framing: the style is added. ``quality`` is
        medium or high (default: the profile's picture_quality). ``name`` is the new file's name without suffix; it
        goes to assets/media/edits/ as a JPEG (a PNG when the profile's picture_format says so) at the video's
        canvas size."""
        root, manifest = resolve_project(project)
        settings = edit_config.load_edit_config(manifest.profile)
        quote = spend.quote_picture(
            root,
            request,
            quality=quality or settings.picture_quality,
            out=_out(name, f".{settings.picture_format}"),
        )
        return _quote(quote, root)

    @server.tool(
        annotations=QUOTE,
        description=(
            "Quote one clip made from the picture ``still`` (a path in the project); ``motion`` says what moves and "
            f"how the camera moves. ``seconds`` is {_clip_lengths()} (the lengths the clip model makes: any other is "
            "refused). Nothing is paid; the clip comes at the video's size."
        ),
    )
    @reported
    async def quote_clip(
        still: str, motion: str, name: str, seconds: int, project: str | None = None
    ) -> dict[str, Any]:
        root, _ = resolve_project(project)
        quote = await spend.quote_clip(root, still, motion, seconds=seconds, out=_out(name, ".mp4"))
        return _quote(quote, root) | {"allowed_seconds": list(generate.allowed_clip_seconds())}

    @server.tool(annotations=PAID)
    @reported
    async def confirm_spend(quote_id: str, project: str | None = None) -> dict[str, Any]:
        """Make the picture or clip a quote describes (a paid call) and record the spend. Refused when the quote
        expired or changed, or when the call would pass the per-call or per-video limit. Confirming the same quote
        twice pays once. A picture comes with a small preview image: look at it before putting the file in a layer
        with replace_media."""
        root, _ = resolve_project(project)
        made = {"project": str(root)} | await spend.confirm(root, quote_id)
        if made["kind"] != "picture":
            return made
        # a picture is returned as a result of its own, to carry the preview beside the same structured answer
        try:
            preview = generate.picture_preview(root / made["path"])
        except OSError:  # the picture is made and paid for; only the look at it is lost
            return made
        made["next"] = LOOK
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(made, indent=2)),
                Image(data=preview, format="jpeg").to_image_content(),
            ],
            structured_content=made,
        )

    @server.tool(annotations=READ_ONLY)
    @reported
    def spend_status(project: str | None = None) -> dict[str, Any]:
        """What this video has spent against its limits: this video's total and today's, every project's spend today,
        and what is left under each limit."""
        root, _ = resolve_project(project)
        return {"project": str(root), "video": spend.spend_state(root)}

    @server.tool(annotations=READ_ONLY)
    @reported
    def style_info(project: str | None = None) -> dict[str, Any]:
        """The profile's picture style and settings: the full prompt frame a new picture is sent in (``<subject>``
        marks where the request goes), its house rules (follow them), the picture quality and format a quote uses,
        and the search preferences. ``style_references`` are stills for a later, paid option and are not used yet."""
        root, manifest = resolve_project(project)
        settings = edit_config.load_edit_config(manifest.profile)
        return {
            "project": str(root),
            "profile": manifest.profile,
            "prompt_frame": generate.picture_prompt("<subject>", manifest.profile),
            "house_rules": settings.house_rules,
            "picture_quality": settings.picture_quality,
            "picture_format": settings.picture_format,
            "search": settings.search.model_dump(),
            "style_references": {"stills": settings.style_references, "used": False},
            "edit_yaml": edit_config.existing_edit_file(manifest.profile),
        }
