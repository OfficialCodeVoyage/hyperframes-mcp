"""Compare the HyperFrames desktop app's tool list with the one this server was reviewed against.

    python scripts/diff_app_tools.py                 # read the installed app's source (no app needs to run)
    python scripts/diff_app_tools.py --app PATH      # another HyperFrames.app
    python scripts/diff_app_tools.py --live          # ask the running app (POST /mcp, tools/list): the authoritative list

Run it after every app update. Exit code 0: the build is as reviewed. 1: a tool the bridge calls is gone (the server
would break), or the app offers a tool nobody has reviewed, or a reviewed tool is gone. The names are printed, never the
app's token. After reviewing a new tool, add it to ``KNOWN_APP_TOOLS`` in ``src/hyperframes_mcp/server/appinfo.py``,
to the fake app (it serves that set), and record the build in ``docs/app-compat.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from hyperframes_mcp.server.appinfo import (
    APP_PATH,
    ASAR,
    KNOWN_APP_TOOLS,
    REQUIRED_APP_TOOLS,
    RUN_SCOPED_APP_TOOLS,
    TESTED_BUILDS,
    installed_build,
    source_tool_names,
)


def compare(seen: set[str], *, static: bool) -> tuple[list[str], list[str]]:
    """(problems, notes) from the tool names the app offers or quotes, against the reviewed set."""
    problems: list[str] = []
    notes: list[str] = []
    missing = sorted(REQUIRED_APP_TOOLS - seen)
    if missing:
        problems.append(f"the bridge calls tools the app no longer offers: {', '.join(missing)}")
    expected = KNOWN_APP_TOOLS | (RUN_SCOPED_APP_TOOLS if static else frozenset())
    new = sorted(seen - expected)
    if new:
        problems.append(f"tools nobody has reviewed (they may spend or write): {', '.join(new)}")
    gone = sorted(KNOWN_APP_TOOLS - seen)
    if gone:
        problems.append(f"reviewed tools the app no longer offers: {', '.join(gone)}")
    if static:
        notes.append(
            "static reading: it lists every name the app's source quotes, including its run-scoped tools "
            f"({', '.join(sorted(RUN_SCOPED_APP_TOOLS))}); confirm a new name with --live before adding it"
        )
    return problems, notes


async def _live() -> set[str]:
    from hyperframes_mcp.server.bridge import AppBridge

    return set(await AppBridge().tool_names())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--app", type=Path, default=APP_PATH, help="the HyperFrames.app to read (default %(default)s)")
    parser.add_argument("--live", action="store_true", help="ask the running app instead of reading its source")
    args = parser.parse_args()

    build = installed_build(args.app)
    print(f"app build: {build or 'unknown'} (reviewed: {', '.join(TESTED_BUILDS)})")
    if build is not None and build not in TESTED_BUILDS:
        print(f"note: build {build} has not been tried; add it to TESTED_BUILDS and docs/app-compat.md once it has")
    if args.live:
        seen = asyncio.run(_live())
    else:
        asar = args.app / ASAR
        if not asar.is_file():
            print(f"error: no app.asar at {asar}", file=sys.stderr)
            return 2
        seen = set(source_tool_names(asar))
    problems, notes = compare(seen, static=not args.live)
    print(f"{len(seen)} tool names seen, {len(KNOWN_APP_TOOLS)} reviewed")
    for note in notes:
        print(f"note: {note}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if not problems:
        print("the tool list is as reviewed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
