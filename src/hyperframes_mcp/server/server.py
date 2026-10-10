"""The MCP server object: its name, the rules every client gets up front, and the tools.

The ``instructions`` are what Claude Code loads before any tool, also in the HyperFrames app's chat, so the house rules
that hold for every video live here; a profile's own rules come from the ``project_info`` tool.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from hyperframes_mcp.server import (
    app_tools,
    context_tools,
    edit_tools,
    media_tools,
    review_tools,
    spend_tools,
)
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.vector_tools import register as register_vector_tools
from hyperframes_mcp.server.version import server_version

NAME = "hyperframes-edit"
KEY_NAMES = ("OPENAI_API_KEY", "FAL_KEY", "ELEVENLABS_API_KEY")

INSTRUCTIONS = """Tools for a composed HyperFrames video project (a HyperFrames folder with video.project.json).
- Call project_info first: it names the project, profile, sources, the spend left, the server's version and the
  profile's house rules (style_info repeats them with the profile's settings). Follow them as you follow these.
- If the hyperframes-edit tools, or one named here, are not in your tool list (search for "hyperframes-edit" first), the chat holds no
  server or an old one: say so in your answer and ask the person to quit the HyperFrames app completely (Cmd+Q) and
  reopen the project, because the chat loads MCP servers only when it starts. Only then use the command fallback in
  VIDEO.md; never fall back to it silently.
- To see what is on screen when, and what the narrator says over it, call layer_table for the stretch you work on;
  do not parse index.html yourself.
- When the person points at something ("fix this", a picked layer, a dragged range), pass the <app_context> block of
  their message to resolve_selection to learn the time, the layers on screen, the sentence spoken and the credits; the
  picked layers come with their own times under picked_layers. A message typed in the chat box carries only the playhead:
  when the person says "this" and no element is named, ask them to pick it on the timeline and send from the card there.
- Start an editing turn with catch_up, so you never write over the person's changes.
- Put a finding that belongs at a time (a credit over a face, a late title) on the person's timeline with
  post_comment; it needs the app running, so say so in your reply when it is not. A comment that asks for a change
  must stand alone, because the app's Apply button gives a fresh agent only its text: say what to change, name the
  layer and pass change=free or change=paid. When you are asked to apply a comment, leave it for the person to
  resolve: the app marks it addressed when your run ends, and resolve_comment while you work ends your run.
- Paid calls are quoted first (quote_picture, quote_clip). Tell the person what you will make and its price, then
  call confirm_spend. The server refuses a call above its limits (dollars, and the number of calls an hour and a day);
  do not try to get around them. Never repeat a paid call on your own. If an answer says a call may have been billed,
  or that paid calls are frozen, stop and tell the person: only they can clear a hold (`hyperframes-mcp spend clear`)
  or thaw the server (`hyperframes-mcp spend thaw`).
- A new picture comes back from confirm_spend with a small preview. Look at the new picture and say, before putting it
  in a layer, whether it matches the profile's look (its house rules say what that is). If it does not, say so and
  offer to remake it (that costs again, so ask first).
- Put a new picture or clip in its layer with replace_media; it sets the credit line too.
- Move, trim, split, duplicate or delete layers with edit_timeline: its default dry run says what each layer would
  become, then call it again with dry_run=false. Change a block's text, number, colour or position with set_variable.
  Add a new counter, ledger, chart, title or label with add_block, and move or resize a layer with arrange_layer;
  narration gives the time each word is said, to land a layer on its word. Never edit index.html by hand or with
  scripts of your own. Each edit is one history entry under the author hyperframes-mcp, and undo takes back your own last
  edits, never the person's.
- For a photo from the free archives, call search_media, tell the person what you found, then fetch_media the one
  they pick: nothing is paid, only CC0 and public domain are found (CC BY when you pass allow_by), and the fetched
  file's record brings its credit, so replace_media needs no credit argument. A licence from the Internet Archive or
  Openverse is only the uploader's word: say the person should confirm it at the source page.
- Every photo or clip keeps a true credit line on screen with it (run_checks checks it); how long it shows is the
  profile's house rule.
- For an icon, arrow, seal or stamp (a vector, not a picture), call search_icons (look at its numbered image before you
  pick: a name can mislead) or write the SVG yourself and save_vector it, then place it with add_graphic; all free.
  vectorize traces a picture of the project into an SVG.
- A date plate or caption that covers the subject is moved, never cut short: its words carry meaning.
- After an edit, call run_checks and look with get_frame or contact_sheet at the seconds you changed (they show the
  HyperFrames app's own preview, so the app must be running with the project open; nothing else is drawn).
  block_info tells where a graphic block sits and how it stacks, so you need not read its code; give it a layer id
  to see what each variable of that layer is set to.
- Never use the app's own music, sound or picture finders for anything in the video: they bring no licence record
  (search_media and fetch_media do).
- Never open .env and never print or ask for a key: the server loads the keys itself.
"""


def build_server(bridge: AppBridge | None = None) -> MCPServer:
    """The server with every tool registered. ``bridge`` reaches the HyperFrames app (tests give one that talks to a
    fake app); each tool that needs the app takes it, and it finds the app anew on every call."""
    server = MCPServer(NAME, instructions=INSTRUCTIONS)
    bridge = bridge or AppBridge()

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    def ping() -> dict[str, Any]:
        """Whether the server runs, which code (git sha, start time, pid), where, and which keys it can see (names
        only, never values)."""
        return {
            "server": NAME,
            "version": server_version(),
            "project_dir": os.environ.get("CLAUDE_PROJECT_DIR"),
            "cwd": str(Path.cwd()),
            "python": sys.executable,
            "keys": {name: bool(os.environ.get(name)) for name in KEY_NAMES},
        }

    for tools in (context_tools, spend_tools, media_tools):
        tools.register(server)
    for tools in (app_tools, edit_tools, review_tools):
        tools.register(server, bridge)
    register_vector_tools(server, bridge)
    return server
