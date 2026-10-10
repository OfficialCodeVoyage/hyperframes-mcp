"""The free-media tools: ``search_media`` finds pictures in the free archives and ``fetch_media`` puts one in the project.

Both are free (the Library of Congress, Wikimedia Commons, the Internet Archive and Openverse need no key) and run the
licence policy of ``hyperframes_mcp.motion.media``: CC0 and public domain by default, CC BY only when asked. A fetched picture carries
its licence record, so ``replace_media`` takes its credit from it. The profile's ``edit.yaml`` sets which sources are asked and
whether CC BY is allowed when a call does not say.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from hyperframes_mcp.motion import generate, media
from hyperframes_mcp.motion.edit_config import load_edit_config
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import resolve_project

# not read-only: both keep a file in the project (the hits found, the picture fetched); neither overwrites or pays
SEARCH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
FETCH = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)
FOUND = (
    "tell the person what you found (title, source, licence) and fetch_media the one they pick, or the best fit if they "
    "left it to you"
)
NOTHING = (
    "nothing usable: try other words (a person, place or year works better than a mood), or allow_by=true to add CC BY; "
    "`tried` says what each source gave"
)
LOOK = (
    "look at the attached preview and say whether it shows what the layer needs; then put it in the layer with "
    "replace_media (its credit comes from the file's record: no credit argument)"
)
VERIFY = "; the licence is what the source states, not something checked here, so tell the person to open source_url and confirm it"
NO_CLIPS = (
    "there is no clip search: the free archives this server asks hold still pictures only. Search for a picture, then "
    "make a clip from it with quote_clip"
)


def register(server: MCPServer) -> None:
    """Add the media tools to ``server``."""

    @server.tool(annotations=SEARCH)
    @reported
    async def search_media(
        query: str,
        kind: str = "photo",
        limit: int = media.DEFAULT_LIMIT,
        allow_by: bool | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Search the free archives (Library of Congress, Wikimedia Commons, Internet Archive, Openverse) for a picture of
        ``query``; nothing is paid. A handful of hits (``limit``, up to 10): a short ``id``, title, source, licence, the
        credit text for the screen, size and date. Only CC0 and public domain are found; ``allow_by=true`` adds CC BY
        (its credit names who made it); share-alike and non-commercial are never found. The profile's edit.yaml
        sets the sources and whether CC BY is allowed when ``allow_by`` is left out. ``kind`` is ``photo``: a ``clip``
        finds nothing, because these archives hold still pictures only. When nothing usable comes back it asks again,
        within a few seconds, the sources that did not fail, with fewer words (names and years first): ``retried`` lists
        those wordings and ``tried`` shows every round. Put a hit in the project with fetch_media."""
        root, manifest = resolve_project(project)
        if kind != "photo":
            raise media.MediaError(f"kind {kind!r}: {NO_CLIPS}")
        prefs = load_edit_config(manifest.profile).search
        found = await media.search(
            root,
            query,
            sources=prefs.sources,
            allow_by=prefs.allow_by if allow_by is None else allow_by,
            limit=limit,
        )
        again = (
            f"; found with fewer words ({found['retried'][-1]}): check it still shows what the layer needs"
            if found["hits"] and found["retried"]
            else ""
        )
        return {
            "project": str(root),
            **found,
            "next": FOUND + again if found["hits"] else NOTHING,
        }

    @server.tool(annotations=FETCH)
    @reported
    async def fetch_media(id: str, name: str, project: str | None = None) -> dict[str, Any]:
        """Download the hit ``id`` of a search_media (this project's latest hundred) into assets/media/found/<name> with
        its licence record beside it, and look at it: the result carries a small preview. ``name`` is the new file's name
        without suffix (the suffix is the picture's own). Then replace_media puts it in a layer and takes its credit
        from the record."""
        root, _ = resolve_project(project)
        made = {"project": str(root)} | await media.fetch(root, id, name)
        made["next"] = LOOK + (VERIFY if made.get("verify_at_source") else "")
        try:
            preview = generate.picture_preview(root / made["path"])
        except OSError:  # the file was checked as a picture; only the look at it is lost
            return made
        return CallToolResult(
            content=[
                TextContent(type="text", text=json.dumps(made, indent=2)),
                Image(data=preview, format="jpeg").to_image_content(),
            ],
            structured_content=made,
        )
