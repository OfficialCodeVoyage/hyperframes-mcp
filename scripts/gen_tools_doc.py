"""Write docs/tools.md and the tools table of README.md from the server's own tool list, so they cannot drift from the code.

python scripts/gen_tools_doc.py            # rewrite docs/tools.md and the README's tools table
python scripts/gen_tools_doc.py --check    # exit 1 when either is out of date (CI and the test suite run this)
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from pathlib import Path

from mcp import Client

from hyperframes_mcp.server.server import NAME, build_server

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "tools.md"
README = ROOT / "README.md"
README_START, README_END = "<!-- tools:start -->", "<!-- tools:end -->"

# the order the page presents the tools in, by what the person is doing
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "Look",
        "Read the project. Nothing here changes it.",
        ("ping", "project_info", "layer_table", "narration", "block_info", "style_info", "spend_status"),
    ),
    (
        "Talk to the app",
        "Work with what the person is doing in the HyperFrames desktop app. These need the app running.",
        (
            "resolve_selection",
            "catch_up",
            "post_comment",
            "list_comments",
            "resolve_comment",
            "get_frame",
            "contact_sheet",
        ),
    ),
    (
        "Edit",
        "Change the project. Each edit is one history entry in the app, under the author `hyperframes-mcp`.",
        (
            "edit_timeline",
            "set_variable",
            "add_block",
            "arrange_layer",
            "replace_media",
            "add_graphic",
            "undo",
            "run_checks",
        ),
    ),
    (
        "Free media",
        "Photos from the free archives, and vectors. Nothing here is paid.",
        ("search_media", "fetch_media", "search_icons", "save_vector", "vectorize"),
    ),
    (
        "Paid pictures and clips",
        "Quote first, then confirm. The limits are enforced by the server: see `docs/spend-safety.md`.",
        ("quote_picture", "quote_clip", "confirm_spend"),
    ),
]


# one line per tool for the README's table (docs/tools.md has the full text)
SUMMARY: dict[str, str] = {
    "ping": "Whether the server runs, which version, which keys it can see (names only).",
    "project_info": "The project, its house rules, sources and the spend left. Claude calls it first.",
    "layer_table": "What is on screen when, with its credits and the sentence spoken over it.",
    "narration": "The time each spoken word is said, to land a layer on its word.",
    "block_info": "What each block is for, where it sits on the frame, and a layer's variables.",
    "style_info": "The profile's picture style and house rules.",
    "spend_status": "What has been spent against the limits, and what is left.",
    "resolve_selection": "What you are pointing at in the app: the time, the layers, the sentence.",
    "catch_up": "What changed since Claude last looked, and who changed it.",
    "post_comment": "Pin a note at a time on your timeline.",
    "list_comments": "The comments on your timeline, with their status.",
    "resolve_comment": "Mark a timeline comment resolved, or open it again.",
    "get_frame": "One frame of the video, from the app's own preview.",
    "contact_sheet": "A labelled grid of frames over a stretch, to review it at a glance.",
    "edit_timeline": "Move, trim, split, duplicate or delete layers (a dry run first).",
    "set_variable": "Change a block's text, number, colour or position.",
    "add_block": "Add a counter, ledger, chart, title, date stamp or lower third.",
    "arrange_layer": "Move or resize a layer.",
    "replace_media": "Put a picture or clip in its layer, with its credit line.",
    "add_graphic": "Place a saved vector (an icon, arrow, seal or stamp) as a new layer.",
    "undo": "Take back Claude's own last edits, never yours.",
    "run_checks": "Check the page after an edit: a true credit line with every picture, no layer hidden behind another.",
    "search_media": "Find CC0 and public-domain photos in four free archives.",
    "fetch_media": "Download the one you pick, with its licence record.",
    "search_icons": "Find an icon in the bundled open sets.",
    "save_vector": "Save an SVG in the project, after a safety check.",
    "vectorize": "Trace a picture of the project into an SVG, on your machine.",
    "quote_picture": "Price a new picture. Nothing is spent.",
    "quote_clip": "Price a short clip made from a picture. Nothing is spent.",
    "confirm_spend": "Make the quoted picture or clip, under the server's limits. Spends money.",
}


def _marked(tool) -> str:
    """The hints Claude Code shows beside a tool (``/mcp``): read-only, or open-world (reaches outside the machine)."""
    hints = tool.annotations
    marks = []
    if hints is not None and hints.read_only_hint:
        marks.append("read-only")
    if hints is not None and hints.open_world_hint:
        marks.append("open-world")
    return ", ".join(marks)


def _type_of(schema: dict) -> str:
    if "enum" in schema:
        return " | ".join(repr(v) for v in schema["enum"])
    if "anyOf" in schema:
        parts = [_type_of(s) for s in schema["anyOf"] if s.get("type") != "null"]
        return " or ".join(parts) + (" or null" if len(parts) != len(schema["anyOf"]) else "")
    kind = schema.get("type", "any")
    if kind == "array":
        return f"list of {_type_of(schema.get('items', {}))}"
    return str(kind)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


async def _tools():
    async with Client(build_server()) as client:
        return (await client.list_tools()).tools


def _served() -> dict:
    """The server's tools by name, checked against GROUPS and SUMMARY."""
    tools = {tool.name: tool for tool in asyncio.run(_tools())}
    placed = {name for _, _, names in GROUPS for name in names}
    unplaced = sorted(set(tools) - placed)
    missing = sorted(placed - set(tools))
    if unplaced or missing:
        raise SystemExit(
            f"scripts/gen_tools_doc.py GROUPS is out of step with the server: not placed {unplaced}, not served {missing}"
        )
    if set(SUMMARY) != set(tools):
        raise SystemExit(
            f"scripts/gen_tools_doc.py SUMMARY is out of step with the server: {sorted(set(SUMMARY) ^ set(tools))}"
        )
    return tools


def render_readme_block(tools: dict) -> str:
    """The tables between the README's markers: every tool in its group, with one line and its Claude Code hints."""
    out = [
        README_START,
        "",
        f"The {len(tools)} tools of the `{NAME}` server, as `/mcp` in Claude Code lists them. Each one is "
        "described in full, with its arguments, in [`docs/tools.md`](docs/tools.md) (generated from the server). "
        "*Read-only* tools change nothing; *open-world* ones reach outside your machine (the free archives, or the "
        "paid provider).",
        "",
    ]
    for title, blurb, names in GROUPS:
        out += [f"**{title}.** {blurb}", "", "| Tool | What it does | Marked |", "|---|---|---|"]
        out += [f"| `{name}` | {SUMMARY[name]} | {_marked(tools[name])} |" for name in names]
        out.append("")
    out.append(README_END)
    return "\n".join(out)


def splice_readme(readme: str, block: str) -> str:
    """``readme`` with the text between its markers replaced by ``block``."""
    start, end = readme.find(README_START), readme.find(README_END)
    if start < 0 or end < start:
        raise SystemExit(f"README.md needs the markers {README_START} and {README_END}")
    return readme[:start] + block + readme[end + len(README_END) :]


def render(tools: dict) -> str:
    out = [
        "# Tools",
        "",
        f"The {len(tools)} tools of the `{NAME}` server. This page is generated from the server's own tool list by "
        "`scripts/gen_tools_doc.py`; do not edit it by hand (`python scripts/gen_tools_doc.py` rewrites it, `--check` fails "
        "when it is stale).",
        "",
    ]
    for title, blurb, names in GROUPS:
        out += [f"## {title}", "", blurb, ""]
        for name in names:
            tool = tools[name]
            out += [f"### `{name}`", "", inspect.cleandoc(tool.description or ""), ""]
            props = (tool.input_schema or {}).get("properties", {})
            required = set((tool.input_schema or {}).get("required", []))
            if props:
                out += ["| Argument | Type | Required | Meaning |", "|---|---|---|---|"]
                for arg, schema in props.items():
                    out.append(
                        f"| `{arg}` | {_cell(_type_of(schema))} | {'yes' if arg in required else 'no'} | "
                        f"{_cell(schema.get('description', ''))} |"
                    )
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="fail when docs/tools.md is out of date")
    args = parser.parse_args()
    tools = _served()
    text = render(tools)
    readme = splice_readme(README.read_text(encoding="utf-8"), render_readme_block(tools))
    if args.check:
        stale = [
            name
            for path, wanted in ((DOC, text), (README, readme))
            if not path.is_file() or path.read_text(encoding="utf-8") != wanted
            for name in (path.relative_to(ROOT),)
        ]
        if stale:
            print(f"{', '.join(map(str, stale))} out of date: run `python scripts/gen_tools_doc.py`", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(text, encoding="utf-8")
    README.write_text(readme, encoding="utf-8")
    print(f"wrote {DOC} ({len(text.splitlines())} lines) and the tools table of {README}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
