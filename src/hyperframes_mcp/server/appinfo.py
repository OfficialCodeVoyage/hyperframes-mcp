"""Which build of the HyperFrames desktop app is installed, and which builds this server has been tried against.

The bridge talks to an endpoint the app does not document, so a new build can change it. The build number is the
``hfDatadog.version`` of the ``package.json`` inside ``app.asar`` (the bundle's own version stays ``0.1.0``). Reading it
needs no network and runs no code of the app.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

APP_PATH = Path("/Applications/HyperFrames.app")
ASAR = Path("Contents/Resources/app.asar")
# builds the server was run against (docs/app-compat.md says how each was tried)
TESTED_BUILDS = ("b276", "b296")
# the tools the app's own endpoint (``POST /mcp``) offered in b296, read with ``tools/list`` on 2026-10-10. A tool not on
# this list is new in a build we have not reviewed: ``doctor`` names it, because the app's endpoint is not ours and a new
# tool may spend or write. (HeyGen's generation tools are not here: the app gives those only to its own agent runs, on a
# route of their own.)
KNOWN_APP_TOOLS = frozenset(
    {
        "agent.cancel", "agent.jobs", "agent.run", "agent.steer", "agent.wait",
        "comments.done", "comments.list", "comments.post", "comments.resolve", "comments.take", "comments.wait",
        "file.read", "file.write",
        "history.begin", "history.end", "history.list", "history.undo",
        "image.cutOut", "image.previewSelection", "image.proposeSelection", "image.removeBackground", "image.removeObject",
        "media.import", "media.library", "media.libraryImport", "media.list",
        "preview.frame",
        "project.create", "project.duplicate", "project.files", "project.list", "project.open", "project.rename",
        "render.start", "render.wait",
        "skill.read", "ui.navigate", "video.removeBackground",
    }
)  # fmt: skip

# the tools this server's bridge calls (``bridge.py``); the compatibility check fails when the app stops offering one
REQUIRED_APP_TOOLS = frozenset(
    {
        "agent.jobs", "comments.list", "comments.post", "comments.resolve", "file.read", "file.write",
        "history.begin", "history.end", "history.list", "history.undo", "preview.frame", "project.list",
    }
)  # fmt: skip
# names the app's source quotes that are not on ``POST /mcp``: they belong to the run-scoped route the app hands only to
# its own agent runs (``/mcp/image/<job>``), which also carries HeyGen's paid generation tools
RUN_SCOPED_APP_TOOLS = frozenset({"app.look"})

_TOOL_NAME = re.compile(r"""["`]([a-z]{2,10}\.[A-Za-z]{2,24})["`]""")
# a quoted ``word.word`` in the source that is a file name, not a tool
_NOT_A_TOOL = frozenset(
    "json mjs js cjs md html css png jpg svg txt log jsonl yaml yml mp4 wav mp3 node map ts sh ico icns webm gif lock "
    "woff2 exe app pdf".split()
)


def _header(handle) -> tuple[dict, int] | None:
    """The JSON header of an open ``app.asar`` and the offset where the files start, or None for a short file."""
    prefix = handle.read(16)
    if len(prefix) < 16:
        return None
    pickle_size = struct.unpack("<I", prefix[4:8])[0]
    json_size = struct.unpack("<I", prefix[12:16])[0]
    return json.loads(handle.read(json_size)), 8 + pickle_size


def _walk(node: dict, prefix: str = ""):
    """Every file entry of an asar header as (path, entry); directories are descended, links are skipped."""
    for name, entry in (node.get("files") or {}).items():
        path = f"{prefix}{name}"
        if "files" in entry:
            yield from _walk(entry, path + "/")
        elif "offset" in entry and not entry.get("unpacked") and "link" not in entry:
            yield path, entry


def read_asar_file(asar: Path, name: str) -> bytes | None:
    """One file of an Electron ``app.asar`` archive by its path inside, or None when the archive lacks it.

    The archive starts with a header of JSON (sizes and offsets of every file); the files follow it.
    """
    with asar.open("rb") as handle:
        opened = _header(handle)
        if opened is None:
            return None
        entry, start = opened
        for part in name.split("/"):
            entry = entry.get("files", {}).get(part)
            if entry is None:
                return None
        if "offset" not in entry or entry.get("unpacked"):
            return None
        handle.seek(start + int(entry["offset"]))
        return handle.read(int(entry["size"]))


def extract_asar(asar: Path, out: Path, *, only: str = "") -> list[str]:
    """Write the files of ``asar`` whose path starts with ``only`` under ``out`` and return their paths.

    Unpacked files and links are skipped, and a path that would leave ``out`` (a ``..`` part, an absolute path) is
    refused with ``ValueError``: the archive is data from an app we do not control.
    """
    written: list[str] = []
    root = out.resolve()
    with asar.open("rb") as handle:
        opened = _header(handle)
        if opened is None:
            return written
        header, start = opened
        for path, entry in _walk(header):
            if not path.startswith(only):
                continue
            target = (root / path).resolve()
            if not target.is_relative_to(root) or target == root:
                raise ValueError(f"the archive holds a path outside the target folder: {path}")
            handle.seek(start + int(entry["offset"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read(int(entry["size"])))
            written.append(path)
    return written


def source_tool_names(asar: Path) -> dict[str, list[str]]:
    """Every tool name the app's MCP code quotes (``area.name``, in ``main/mcp*.mjs``), with the files that quote it.

    This is a reading of source text, so it also finds tools of the app's run-scoped route (``RUN_SCOPED_APP_TOOLS``);
    the live ``tools/list`` of the running app is what decides what is offered on ``POST /mcp``.
    """
    found: dict[str, list[str]] = {}
    with asar.open("rb") as handle:
        opened = _header(handle)
        if opened is None:
            return found
        header, start = opened
        for path, entry in _walk(header):
            name = path.rsplit("/", 1)[-1]
            if not (path.startswith("main/") and name.startswith("mcp") and name.endswith(".mjs")):
                continue
            handle.seek(start + int(entry["offset"]))
            text = handle.read(int(entry["size"])).decode("utf-8", "replace")
            for tool in set(_TOOL_NAME.findall(text)):
                if tool.split(".")[1] not in _NOT_A_TOOL:
                    found.setdefault(tool, []).append(name)
    return {tool: sorted(files) for tool, files in sorted(found.items())}


def installed_build(app: Path = APP_PATH) -> str | None:
    """The build number of the installed app (``"b296"``), or None when the app or the number is not there."""
    try:
        raw = read_asar_file(app / ASAR, "package.json")
        if raw is None:
            return None
        build = json.loads(raw).get("hfDatadog", {}).get("version")
    except (OSError, ValueError, AttributeError, struct.error):
        return None
    return build if isinstance(build, str) else None
