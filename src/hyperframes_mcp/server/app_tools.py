"""The tools that go through the HyperFrames app's own endpoint (``bridge.AppBridge``): what the person picked, what
changed since the agent last looked, and comments on the person's timeline.

Each says clearly when the app is not running. ``catch_up`` has a disk route; a comment has none, since it is a note on
the person's timeline in the app, so it is never faked. ``resolve_comment`` marks one handled, but never one the app's
agent is working on or has queued: resolving it ends that run.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from hyperframes_mcp.motion import project_edit
from hyperframes_mcp.server import catch_up as changes
from hyperframes_mcp.server.bridge import (
    AppBridge,
    AppError,
    AppProjectUnknown,
    AppUnavailable,
)
from hyperframes_mcp.server.errors import reported
from hyperframes_mcp.server.project import resolve_project
from hyperframes_mcp.server.selection import (
    SelectionError,
    context_from_snapshot,
    parse_app_context,
    resolve,
)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
POST = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
MAX_BODY = 4000
MAX_THREADS = 30
# what the app's agent is told when the person presses Apply on a comment: it starts a fresh session that sees only the
# comment's text, so a comment that asks for a change carries how to make it
_APPLY = (
    "[To apply: use the hyperframes-edit tools only, never edit index.html by hand; if it needs a picture or clip made with "
    "quote_picture or quote_clip, stop and say so, because that costs money; leave this comment for the person to "
    "resolve.]"
)
_APPLY_PAID = (
    "[To apply (PAID, it costs money): use the hyperframes-edit tools only, never edit index.html by hand; quote first "
    "(quote_picture or quote_clip), give the person the price in your reply and stop: do not call confirm_spend "
    "until they say yes; leave this comment for the person to resolve.]"
)


# the apply line at the end of a comment, whichever wording post_comment had when it was posted
_APPLY_LINE = re.compile(r"\s*\[To apply[^\]]*\]\s*$")
# the states of an app run that resolving its comment cancels (the app's own rule); a run still "waiting" for the
# person's Send is left alone
_CANCELLED_BY_RESOLVE = frozenset({"queued", "running"})


def _comments_of(job: dict[str, Any]) -> list[str]:
    """The comment threads an app run carries: ``threadIds`` (build b296 on: comments sent together are one run) or the
    ``threadId`` of earlier builds."""
    ids = job.get("threadIds")
    if isinstance(ids, list):
        return [i for i in ids if isinstance(i, str)]
    return [job["threadId"]] if isinstance(job.get("threadId"), str) else []


def _without_apply_line(text: str) -> str:
    """A comment's text without the apply line ``post_comment`` ends it with (it is for the app's agent; the person and
    a listing want the words and the layer)."""
    return _APPLY_LINE.sub("", text)


def register(server: MCPServer, bridge: AppBridge) -> None:
    """Add the app tools to ``server``; they reach the app through ``bridge``."""

    @server.tool(annotations=READ_ONLY)
    @reported
    async def resolve_selection(
        app_context: str | None = None,
        use_app: bool = False,
        project: str | None = None,
    ) -> dict[str, Any]:
        """What the person is pointing at, as what is on screen there. Pass the ``<app_context>`` block of their
        message (a picked element, a timeline range, marks they drew, or only where the playhead stood), or
        ``use_app`` to ask the app what is picked now. Gives the time or range, the layers on screen then (id, kind,
        file, text, credits), the shots, the narration sentence spoken and the credit lines shown. Each layer the pick names
        comes again under ``picked_layers`` with its own time, file, credits and narration (a pick made on the timeline
        carries the playhead's moment, so the picked clip is often not on screen there).
        """
        root, _ = resolve_project(project)
        if app_context:
            context, source = parse_app_context(app_context), "app_context"
        elif use_app:
            try:
                snapshot = await bridge.selection(await bridge.project_id(root))
            except AppUnavailable as error:
                raise AppUnavailable(f"{error}; pass the <app_context> block of the person's message instead") from None
            if snapshot is None:
                return {
                    "project": str(root),
                    "source": "app",
                    "selected": False,
                    "note": "nothing is picked in the app now: ask the person to click an element, or pass the "
                    "<app_context> block of their message",
                }
            context, source = context_from_snapshot(snapshot), "app"
        else:
            raise SelectionError("pass app_context (the block in the person's message) or use_app=true")
        return {"source": source, **resolve(root, context)}

    @server.tool(annotations=READ_ONLY)
    @reported
    async def catch_up(since: str | None = None, project: str | None = None) -> dict[str, Any]:
        """What changed in the project since you last looked, and who changed it. With the app running: its history
        entries (who, when, label, files; ``since`` is an entry id, an ISO time or ``mine``, the default: after your
        own last entry) and the runs of its own agent. With the app closed: the project's git log and the files
        written since (``since`` an ISO time, default the last 24 hours). Call it at the start of a turn, before you
        edit, so you never write over the person's changes.
        """
        root, _ = resolve_project(project)
        when = changes.parse_since(since)
        found: dict[str, Any] = {"project": str(root)}
        try:
            project_id = await bridge.project_id(root)
            history = await bridge.history_list(project_id, since, limit=20)
        except (AppUnavailable, AppProjectUnknown) as error:
            found |= {"route": "disk", "why": str(error)}
            found |= changes.disk_changes(root, when)
        else:
            found |= {
                "route": "app",
                "since": since or "mine",
                "history": changes.entries(history),
            }
        found["runs"] = changes.agent_runs(root, when)
        return found

    @server.tool(annotations=POST)
    @reported
    async def post_comment(
        time: float,
        body: str,
        layer: str | None = None,
        change: Literal["none", "free", "paid"] = "none",
        project: str | None = None,
    ) -> dict[str, Any]:
        """Pin a note at ``time`` (seconds) on the person's timeline in the app, for what you found while checking
        (a credit that covers a face, a title that comes late). It waits in the app's chat until the person
        addresses it. Needs the app running: when it is not, nothing is saved and you tell the person in your reply.

        When the person presses Apply on a comment, the app starts a fresh agent session that sees only the comment's
        text. So for a comment that asks for a change, say what to change in words that stand alone, name the ``layer``
        (its id, which must be on the page) and set ``change`` to ``free`` (made with the free tools) or ``paid`` (a
        new picture or clip: the comment then tells that agent to quote first and not pay before the person's yes). A
        finding alone is ``none``; a ``layer`` is named in the comment whatever ``change`` is.
        """
        if not body.strip() or len(body) > MAX_BODY:
            raise AppError(f"a comment is 1 to {MAX_BODY} characters")
        root, _ = resolve_project(project)
        if layer and layer not in project_edit.mounts((root / "index.html").read_text(encoding="utf-8")):
            raise project_edit.EditError(f"no layer {layer!r} in index.html: layer_table gives the ids")
        tail = [f"Layer: {layer}."] if layer else []
        if change != "none":
            tail.append(_APPLY_PAID if change == "paid" else _APPLY)
        text = f"{body.rstrip()}\n\n{' '.join(tail)}" if tail else body
        if len(text) > MAX_BODY:
            added = len(text) - len(body.rstrip())
            raise AppError(
                f"a comment is 1 to {MAX_BODY} characters with its layer and apply line: here they take {added}, "
                f"so the body is at most {MAX_BODY - added}, not {len(body.rstrip())}"
            )
        try:
            posted = await bridge.comments_post(await bridge.project_id(root), text, time)
        except AppUnavailable as error:
            raise AppUnavailable(
                f"no comment was posted: {error}. Comments live on the person's timeline in the app; tell them "
                "in your reply instead"
            ) from None
        return {
            "project": str(root),
            "time": time,
            "thread_id": posted.get("threadId"),
            "number": posted.get("number"),
            "status": posted.get("status", "waiting"),
            "note": "it is on the timeline and waits in the app's chat until the person addresses it",
        }

    @server.tool(annotations=READ_ONLY)
    @reported
    async def list_comments(status: str = "all", project: str | None = None) -> dict[str, Any]:
        """The comments on the person's timeline (yours and theirs), newest first: number, time, status (``open``,
        ``in_progress``, ``addressed``, ``declined`` or ``resolved``), the text and the reply. ``status``
        keeps only those. Needs the app running.
        """
        root, _ = resolve_project(project)
        try:
            threads = await bridge.comments_list(await bridge.project_id(root), status)
        except AppUnavailable as error:
            raise AppUnavailable(f"comments could not be listed: {error}") from None
        threads.sort(key=lambda t: t.get("createdAt", 0), reverse=True)
        return {
            "project": str(root),
            "count": len(threads),
            "comments": [
                {
                    "id": t["id"],
                    "number": t.get("number"),
                    "time": (t.get("anchor") or {}).get("time", (t.get("anchor") or {}).get("start")),
                    "status": t["status"],
                    "text": changes.cut(_without_apply_line(t["body"]), 200),
                    **({"reply": changes.cut(t["reply"], 200)} if t.get("reply") else {}),
                }
                for t in threads[:MAX_THREADS]
            ],
        }

    @server.tool(annotations=POST)
    @reported
    async def resolve_comment(comment: int | str, reopen: bool = False, project: str | None = None) -> dict[str, Any]:
        """Mark a comment on the person's timeline resolved (``comment`` is its number from list_comments, e.g. 4), or
        open it again with ``reopen``. The comment is kept, not deleted. Use it only when the person asks, or for a
        finding of your own that is settled. Never for a comment you were asked to apply: the app marks that one
        addressed when your run ends and the person resolves it, and resolving it while you work ends your run, so your
        answer is lost. A comment the app's agent is working on or has queued is refused. Needs the app running.
        """
        root, _ = resolve_project(project)
        try:
            project_id = await bridge.project_id(root)
            threads = await bridge.comments_list(project_id, "all")
            wanted = str(comment).lstrip("#")
            found = next(
                (t for t in threads if str(t.get("number")) == wanted or t["id"] == wanted),
                None,
            )
            if found is None:
                numbers = ", ".join(str(t.get("number")) for t in threads) or "none"
                raise AppError(f"no comment {comment!r}; the numbers are: {numbers}")
            # a run that is queued has not started, so its thread still reads "open"; the app's job list shows it
            unchecked = None
            held = found.get("status") == "in_progress"
            if not held and not reopen:  # the app cancels a run only when a comment is resolved
                try:
                    held = any(
                        found["id"] in _comments_of(job) and job.get("status") in _CANCELLED_BY_RESOLVE
                        for job in await bridge.agent_jobs(project_id)
                    )
                except AppError as error:
                    unchecked = str(error)
            if held:
                raise AppError(
                    f"comment {found.get('number')} has an Apply run on it (running or queued in the app): changing "
                    "its status ends that run (the app cancels it) and its answer is lost. Leave it: the app marks "
                    "it addressed when the run ends, and the person resolves it"
                )
            changed = await bridge.comments_resolve(project_id, found["id"], not reopen)
        except AppUnavailable as error:
            raise AppUnavailable(f"the comment was not changed: {error}") from None
        return {
            "project": str(root),
            "number": found.get("number"),
            "status": "open" if reopen else "resolved",
            "changed": changed,
            **(
                {
                    "note": f"the app's runs could not be listed ({unchecked}), so a queued run on this comment "
                    "could not be ruled out"
                }
                if unchecked
                else {}
            ),
        }
