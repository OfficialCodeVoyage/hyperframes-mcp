"""Copy the readable source of the installed HyperFrames desktop app into a folder, to read how its endpoint changed.

    python scripts/extract_app.py OUT_DIR [--app PATH] [--only main/]

Reads ``app.asar`` with the same plain reader the server uses (no Electron, no npm, no code of the app is run) and
writes the files whose path starts with ``--only`` (default ``main/``, where the app's MCP server, comments and agent
jobs live). Read ``main/mcpServer.mjs``, ``main/mcp*Tools.mjs``, ``main/comments.mjs`` and ``main/agentJobs.mjs`` against
``docs/app-compat.md``. The files are the app's, not ours: keep them out of this repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hyperframes_mcp.server.appinfo import APP_PATH, ASAR, extract_asar, installed_build


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("out", type=Path, help="an empty folder to write into")
    parser.add_argument("--app", type=Path, default=APP_PATH, help="the HyperFrames.app to read (default %(default)s)")
    parser.add_argument("--only", default="main/", help="write only the files under this path (default %(default)s)")
    args = parser.parse_args()
    asar = args.app / ASAR
    if not asar.is_file():
        print(f"error: no app.asar at {asar}", file=sys.stderr)
        return 2
    if args.out.exists() and any(args.out.iterdir()):
        print(f"error: {args.out} is not empty", file=sys.stderr)
        return 2
    written = extract_asar(asar, args.out, only=args.only)
    print(f"build {installed_build(args.app) or 'unknown'}: wrote {len(written)} files under {args.only} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
