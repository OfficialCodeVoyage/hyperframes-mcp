"""Write docs/tools.md from the server's own tool list, so the page cannot drift from the code.

    python scripts/gen_tools_doc.py            # rewrite docs/tools.md
    python scripts/gen_tools_doc.py --check    # exit 1 when docs/tools.md is out of date (CI and the test suite run this)
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from pathlib import Path

from mcp import Client

from hyperframes_mcp.server.server import NAME, build_server

DOC = Path(__file__).resolve().parents[1] / "docs" / "tools.md"

# the order the page presents the tools in, by what the person is doing
GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("Look", "Read the project. Nothing here changes it.",
     ("ping", "project_info", "layer_table", "narration", "block_info", "style_info", "spend_status")),
    ("Talk to the app", "Work with what the person is doing in the HyperFrames desktop app. These need the app running.",
     ("resolve_selection", "catch_up", "post_comment", "list_comments", "resolve_comment", "get_frame", "contact_sheet")),
    ("Edit", "Change the project. Each edit is one history entry in the app, under the author `hyperframes-mcp`.",
     ("edit_timeline", "set_variable", "add_block", "arrange_layer", "replace_media", "add_graphic", "undo", "run_checks")),
    ("Free media", "Photos from the free archives, and vectors. Nothing here is paid.",
     ("search_media", "fetch_media", "search_icons", "save_vector", "vectorize")),
    ("Paid pictures and clips", "Quote first, then confirm. The limits are enforced by the server: see `docs/spend-safety.md`.",
     ("quote_picture", "quote_clip", "confirm_spend")),
]


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


def render() -> str:
    tools = {tool.name: tool for tool in asyncio.run(_tools())}
    placed = {name for _, _, names in GROUPS for name in names}
    unplaced = sorted(set(tools) - placed)
    missing = sorted(placed - set(tools))
    if unplaced or missing:
        raise SystemExit(
            f"scripts/gen_tools_doc.py GROUPS is out of step with the server: not placed {unplaced}, not served {missing}"
        )
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
    text = render()
    if args.check:
        if not DOC.is_file() or DOC.read_text(encoding="utf-8") != text:
            print("docs/tools.md is out of date: run `python scripts/gen_tools_doc.py`", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(text, encoding="utf-8")
    print(f"wrote {DOC} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
