"""One history entry of ours around every change to a project's page, so the person can undo it alone.

``turn`` opens the entry (``hyperframes-mcp`` is its author) and closes it when the edit is done; ``Turn.edit`` changes
``index.html`` inside it. ``undo_ours`` takes our newest entries back, never anyone else's.

Two routes, chosen by ``app_project``:
- **The app** is running and lists the project: the entry is the app's own history (``history.begin`` and ``history.end``),
  so the person sees it under ``hyperframes-mcp`` and undoes it from the app or from here. The page is written through its
  version-checked ``file.write``, so an edit the person made meanwhile is never written over: on a stale version the page
  is read again, the change redone, and written once more; a second conflict is an error.
- **Disk**, with the app closed: the HyperFrames CLI's own ``history begin``, ``end`` and ``undo`` under the same author,
  in the log the app shares (the CLI refuses ``history`` while the app has the project open), and an atomic write.

What an entry holds, read from the app's code (``@hyperframes/studio-server`` ``ProjectHistory.beginWindow``): every change
to the project's files between ``begin`` and ``end``, whichever process wrote them: ``end`` sweeps the folder (and so a page
that the CLI wrote on disk meanwhile is in it) and files the changes under the window's author, less what the person saved
in the meantime. So a CLI step inside a turn needs no write through the app to be ours.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypeVar

from hyperframes_mcp.motion import hf_cli
from hyperframes_mcp.motion.project_edit import EditError
from hyperframes_mcp.server.bridge import (
    AUTHOR,
    AppBridge,
    AppConflict,
    AppError,
    AppProjectUnknown,
    AppUnavailable,
)
from hyperframes_mcp.util.atomic import atomic_write_bytes

PAGE = "index.html"
LABEL_CHARS = 100
# a time before any entry: ``history_list`` from it lists everything
EVER = "1970-01-01T00:00:00Z"
T = TypeVar("T")


async def app_project(bridge: AppBridge, root: Path) -> str | None:
    """The app's id for ``root`` when the app answers and lists the project, else None (the disk route)."""
    if not await bridge.available():
        return None
    try:
        return await bridge.project_id(root)
    except (AppUnavailable, AppProjectUnknown):
        return None


class Turn:
    """An open history entry of ours. ``route`` is ``app`` or ``disk``; ``entry`` is the entry once the turn has ended
    (None when it changed nothing)."""

    def __init__(self, bridge: AppBridge, root: Path, project_id: str | None) -> None:
        self.bridge, self.root, self.project_id = bridge, root, project_id
        self.entry: dict[str, Any] | None = None

    @property
    def route(self) -> str:
        return "disk" if self.project_id is None else "app"

    async def edit(self, change: Callable[[str], tuple[str, T]]) -> T:
        """Change ``index.html``: ``change`` gets its text and returns the new text and a result. Through the app, a page
        that changed since it was read is read again and ``change`` is run on it once more.

        Raises:
            EditError: the person kept editing the page while it was being written, twice in a row.
        """
        if self.project_id is None:
            path = self.root / PAGE
            old = path.read_text(encoding="utf-8")
            new, result = change(old)
            if new != old:
                atomic_write_bytes(path, new.encode("utf-8"))
            return result
        for _ in range(2):
            read = await self.bridge.file_read(self.project_id, PAGE)
            new, result = change(read.content)
            if new == read.content:
                return result
            try:
                await self.bridge.file_write(self.project_id, PAGE, new, read.version)
            except AppConflict:
                continue
            return result
        raise EditError(
            f"{PAGE} changed twice while it was being written (the person is editing it): the page was not written (undo takes back what this step already put in the project); "
            "call again in a moment"
        )

    async def _end(self) -> dict[str, Any] | None:
        if self.project_id is None:
            return await asyncio.to_thread(hf_cli.history_end, self.root)
        return await self.bridge.history_end(self.project_id)


def recorded(turn: Turn) -> dict[str, Any]:
    """What a result says of the history entry an edit made: the route it went and the entry's id."""
    return {"route": turn.route, "history_entry": turn.entry and turn.entry["id"]}


@asynccontextmanager
async def turn(bridge: AppBridge, root: Path, label: str) -> AsyncIterator[Turn]:
    """Everything written to the project inside the block is one history entry of ours, ``label`` its name. The turn
    ends when the block does, also when it fails: what was written stays an entry the person (or ``undo``) can take back.

    Raises:
        EditError: the history could not be opened (nothing was changed): the CLI refuses it while the app has the
            project open, and the app did not answer.
    """
    label = " ".join(label.split())[:LABEL_CHARS]
    project_id = await app_project(bridge, root)
    if project_id is not None:
        await bridge.history_begin(project_id, label)
    else:
        try:
            await asyncio.to_thread(hf_cli.history_begin, root, AUTHOR, label)
        except hf_cli.HyperframesError as error:
            raise EditError(f"the edit was not made: its history entry could not be opened ({error})") from error
    current = Turn(bridge, root, project_id)
    try:
        yield current
    except BaseException:
        with contextlib.suppress(AppError, hf_cli.HyperframesError):
            await current._end()
        raise
    current.entry = await current._end()


async def undo_ours(bridge: AppBridge, root: Path, steps: int) -> dict[str, Any]:
    """Take back our ``steps`` newest entries still in effect, newest first; never anyone else's. Stops, saying why, at the
    first it cannot take back: a file it changed was changed again since, by the person or anything outside, and only the
    person may override that.

    Raises:
        EditError: nothing was undone (nothing of ours is left, or the first one is blocked), with the reason.
    """
    project_id = await app_project(bridge, root)
    undone: list[str] = []
    stopped = None
    for _ in range(steps):
        try:
            answer = await _undo(bridge, root, project_id)
        except (AppError, hf_cli.HyperframesError) as error:
            if "no change to undo" not in str(error) and "no entry still in effect" not in str(error):
                raise
            stopped = "nothing of yours is left to undo"
            break
        if answer["ok"]:
            undone.append(answer["entry"]["label"].removeprefix("Undid: "))
            continue
        if "conflict" in answer:
            stopped = await _blocked(bridge, root, project_id, answer["conflict"])
        else:  # the app declined: its own words say why
            stopped = str(answer.get("reason") or "the app did not take it back")
        break
    if not undone:
        raise EditError(f"nothing was undone: {stopped}")
    return {
        "route": "disk" if project_id is None else "app",
        "undone": undone,
        "left": _ours_in_effect(await _listed(bridge, root, project_id)),
        **({"stopped": stopped} if stopped else {}),
    }


async def _undo(bridge: AppBridge, root: Path, project_id: str | None) -> dict[str, Any]:
    if project_id is None:
        return await asyncio.to_thread(hf_cli.history_undo, root, AUTHOR)
    return await bridge.history_undo(project_id)


async def _listed(bridge: AppBridge, root: Path, project_id: str | None) -> list[dict[str, Any]]:
    """The project's history entries, newest first (the last hundred)."""
    if project_id is None:
        return await asyncio.to_thread(hf_cli.history_list, root, limit=100)
    return await bridge.history_list(project_id, since=EVER, limit=100)


def _ours_in_effect(entries: list[dict[str, Any]]) -> int:
    """How many entries of ours are in effect: not undone, and not themselves an undo or a redo."""
    return sum(
        entry["who"]["name"] == AUTHOR and not entry["undone"] and not entry["label"].startswith(("Undid: ", "Redid: "))
        for entry in entries
    )


async def _blocked(bridge: AppBridge, root: Path, project_id: str | None, conflict: dict[str, Any]) -> str:
    """Why an entry of ours cannot be undone: which files changed after it, and who changed them."""
    newer = set(conflict.get("newer") or [])
    names = sorted({_who(entry) for entry in await _listed(bridge, root, project_id) if entry["id"] in newer})
    by = f", by {' and '.join(names)}" if names else ""
    return (
        f"{', '.join(conflict['files'])} changed after your edit{by}; undoing it would lose that change. "
        "Ask the person to undo it in the app, or say what to put back"
    )


def _who(entry: dict[str, Any]) -> str:
    kind = entry["who"]["kind"]
    if kind == "person":
        return "the person"
    return "something outside the app" if kind == "outside" else entry["who"]["name"]
