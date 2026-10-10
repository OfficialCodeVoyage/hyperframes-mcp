"""A fake of the HyperFrames desktop app's local endpoint, for the tests of the bridge and of every tool that uses it.

It is an ``httpx.MockTransport``: no network and no port. It writes the two files the bridge discovers the app by (the
port file and the token file) into a folder of the test's own, and answers what the real app answers, as read from the
app's code and from read-only calls to the live app (2026-10-07):

- ``POST /mcp``: a stateless MCP request (JSON-RPC ``tools/call``) that needs ``Authorization: Bearer <token>`` and an
  ``Accept`` of both ``application/json`` and ``text/event-stream``. A tool's answer is one text part holding JSON; a
  refusal is the same with ``isError``. The tools: ``project.list``, ``file.read``, ``file.write``,
  ``history.begin|end|list|undo``, ``comments.post|list``, ``agent.jobs`` and ``preview.frame``.
- ``GET /api/projects/<id>/selection``: ``{"selection": <snapshot or null>, "updatedAt": ...}``.

Files are real: ``file.read`` and ``file.write`` act on the project's folder on disk, with the app's versions
(``"sha256:<hex>"``, an ETag) and its refusal of a stale one. The history is the app's model, kept short: an entry per
author and turn, undone only by its own author. A turn's entry is every change to the project's files between
``history.begin`` and ``history.end`` (the real app sweeps the folder when a window closes, read from its code), so a
file another process wrote, such as the HyperFrames CLI, lands in it too. What a test arranges: ``select`` (what the
person picked), ``person_edit`` (a change made by hand), ``quit`` (the app goes away), ``after_read`` (a hook called when
a file has been read, to edit it then) and ``calls`` (every request the app saw).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from hyperframes_mcp.server.appinfo import KNOWN_APP_TOOLS

# not a real token: the bridge must never show it
TOKEN = "fake-app-token-0123456789abcdef"  # scan:ignore
PORT = 54321
PNG = b"\x89PNG\r\n\x1a\n" + b"a frame"
# the app's own words (main/mcpFileTools.mjs, main/openProjects.mjs), so the bridge matches them as it matches the app's
CONFLICT = (
    "Nothing written: {path} changed since your read (the person or another agent edited it). Read it again with "
    "file.read, reapply your change, and write with the new version."
)
UNKNOWN_PROJECT = 'No project with id "{id}" in Home. Use an id from project.list (its "id" field).'
SIGN_IN_FIRST = "Sign in to HyperFrames first."
PAGE = 100


class Raw(list):
    """Content parts a tool answers as they are (a picture), not as one text part of JSON."""


class ToolRefused(Exception):
    """A tool's refusal: its message goes to the caller as an ``isError`` answer."""


@dataclass
class Call:
    """One request the fake saw."""

    tool: str
    arguments: dict[str, Any]
    user_agent: str


def version_of(content: str) -> str:
    return '"sha256:' + hashlib.sha256(content.encode()).hexdigest() + '"'


def sender_of(user_agent: str | None) -> str:
    """The author the app files a call under: the first word of the User-Agent (main/mcpServer.mjs ``senderOf``)."""
    match = re.match(r"[^/\s]+", user_agent or "")
    name = match.group(0) if match else ""
    if re.search("codex", name, re.IGNORECASE):
        return "Codex"
    if re.search("claude", name, re.IGNORECASE):
        return "Claude"
    return name or "another app"


class FakeApp:
    """The app, open on ``projects`` (id -> the folder Home records, which may be a link to the real one)."""

    def __init__(
        self,
        support: Path,
        projects: dict[str, Path],
        *,
        port: int = PORT,
        token: str = TOKEN,
    ) -> None:
        self.support = support
        self.offered: list[str] = sorted(KNOWN_APP_TOOLS)  # what tools/list answers
        self.projects = projects
        self.port = port
        self.token = token
        self.running = True
        self.signed_in = True
        self.hang = False
        self.busy_for = 0  # the next this-many requests get no answer in time, then it answers again
        self.calls: list[Call] = []
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.turns: dict[tuple[str, str], dict[str, Any]] = {}
        # called with (project id, path) after file.read answers: a test makes the person edit just then
        self.after_read: Callable[[str, str], None] | None = None
        self.threads: dict[str, list[dict[str, Any]]] = {}
        # the app's agent runs: {"id", "kind", "status", "threadId"} (a comment's run names its thread)
        self.jobs: dict[str, list[dict[str, Any]]] = {}
        self.selections: dict[str, dict[str, Any]] = {}
        self.clock = 1_791_000_000_000.0
        self.transport = httpx.MockTransport(self._handle)
        self.write_discovery_files()

    # ----- what a test arranges

    def write_discovery_files(self) -> None:
        """The files the bridge finds the app by, as the real app writes them."""
        self.support.mkdir(parents=True, exist_ok=True)
        (self.support / "studio-port.json").write_text(json.dumps({"port": self.port}))
        (self.support / "mcp.json").write_text(json.dumps({"token": self.token}))

    def quit(self) -> None:
        """The app is closed: its port files stay (they are stale), nothing answers."""
        self.running = False

    def select(self, project_id: str, **snapshot: Any) -> None:
        """The person picks an element: what ``GET /selection`` then answers."""
        self.selections[project_id] = {
            "schemaVersion": 1,
            "projectId": project_id,
            "compositionPath": "index.html",
            "sourceFile": "index.html",
            "label": "",
            "tagName": "div",
            "thumbnailUrl": "",
            "currentTime": 0,
            "target": {},
            "textContent": None,
            **snapshot,
        }

    def person_edit(self, project_id: str, path: str, content: str) -> None:
        """The person changes a file by hand: it is written and filed under "You"."""
        self._write(
            project_id,
            path,
            content,
            {"kind": "person", "name": "You"},
            f"Edited {path}",
        )

    def project_dir(self, project_id: str) -> Path:
        return self.projects[project_id]

    # ----- HTTP

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if not self.running or request.url.port != self.port:
            raise httpx.ConnectError("connection refused", request=request)
        if self.hang or self.busy_for:
            self.busy_for = max(0, self.busy_for - 1)
            raise httpx.ReadTimeout("no answer", request=request)
        if request.url.path == "/mcp":
            return self._mcp(request)
        match = re.fullmatch(r"/api/projects/([^/]+)/selection", request.url.path)
        if match and request.method == "GET":
            return self._selection(match.group(1))
        return httpx.Response(404)

    def _mcp(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401)
        if not self.signed_in:
            error = {"code": -32001, "message": SIGN_IN_FIRST}
            return httpx.Response(403, json={"jsonrpc": "2.0", "id": None, "error": error})
        accept = request.headers.get("accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            return httpx.Response(406)
        body = json.loads(request.content)
        if body.get("method") == "tools/list":
            tools = [{"name": name} for name in self.offered]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}})
        params = body["params"]
        arguments = params.get("arguments") or {}
        user_agent = request.headers.get("user-agent", "")
        self.calls.append(Call(params["name"], arguments, user_agent))
        handler = getattr(self, "tool_" + params["name"].replace(".", "_"), None)
        try:
            if handler is None:
                raise ToolRefused(f"MCP error -32602: Tool {params['name']} not found")
            value = handler(arguments, sender_of(user_agent))
        except ToolRefused as refused:
            result = {
                "content": [{"type": "text", "text": str(refused)}],
                "isError": True,
            }
        else:
            result = {"content": value if isinstance(value, Raw) else [{"type": "text", "text": json.dumps(value)}]}
        return httpx.Response(200, json={"result": result, "jsonrpc": "2.0", "id": body["id"]})

    def _selection(self, project_id: str) -> httpx.Response:
        if not self.signed_in:
            return httpx.Response(403, json={"ok": False, "error": SIGN_IN_FIRST})
        if project_id not in self.projects:
            return httpx.Response(404, json={"error": "not found"})
        selection = self.selections.get(project_id)
        return httpx.Response(
            200,
            json={
                "selection": selection,
                "updatedAt": "2026-10-07T10:00:00.000Z" if selection else None,
            },
        )

    # ----- tools

    def _dir(self, arguments: dict[str, Any]) -> tuple[str, Path]:
        project_id = arguments["projectId"]
        if project_id not in self.projects:
            raise ToolRefused(UNKNOWN_PROJECT.format(id=project_id))
        return project_id, self.projects[project_id]

    def tool_project_list(self, arguments: dict[str, Any], who: str) -> list[dict]:
        found = sorted(self.projects.items())
        offset, limit = arguments.get("offset", 0), arguments.get("limit", PAGE)
        return [
            {
                "id": id,
                "name": id,
                "path": str(path),
                "openedAt": "2026-10-07T10:00:00Z",
            }
            for id, path in found[offset : offset + limit]
        ]

    def tool_file_read(self, arguments: dict[str, Any], who: str) -> dict:
        _, folder = self._dir(arguments)
        path = folder / arguments["path"]
        if not path.is_file():
            raise ToolRefused(f'No file "{arguments["path"]}" in this project. project.files lists them.')
        content = path.read_text("utf-8")
        answer = {
            "path": arguments["path"],
            "content": content,
            "version": version_of(content),
        }
        if self.after_read:
            self.after_read(arguments["projectId"], arguments["path"])
        return answer

    def tool_file_write(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, folder = self._dir(arguments)
        path, version = arguments["path"], arguments["version"]
        target = folder / path
        current = version_of(target.read_text("utf-8")) if target.is_file() else None
        if (version is None and current is not None) or (version is not None and version != current):
            raise ToolRefused(CONFLICT.format(path=path))
        self._write(
            project_id,
            path,
            arguments["content"],
            {"kind": "agent", "name": who},
            f"file.write {path}",
        )
        return {"path": path, "version": version_of(arguments["content"])}

    def _write(self, project_id: str, path: str, content: str, who: dict, label: str) -> None:
        """A file write: part of the author's open turn (its end files it), else an entry of its own, and no other
        author's open turn takes it in."""
        target = self.projects[project_id] / path
        before = target.read_text("utf-8") if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, "utf-8")
        if (who["name"], project_id) in self.turns:
            return
        for (_, open_in), turn in self.turns.items():
            if open_in == project_id:
                turn["snapshot"][path] = content
        self._file(project_id, who, label, {path: [before, content]})

    @staticmethod
    def _snapshot(folder: Path) -> dict[str, str]:
        """The files the history keeps (none in a folder that starts with a dot), by path, as text that keeps any bytes."""
        return {
            path.relative_to(folder).as_posix(): path.read_bytes().decode("utf-8", "surrogateescape")
            for path in sorted(folder.rglob("*"))
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(folder).parts)
        }

    def _file(
        self,
        project_id: str,
        who: dict,
        label: str,
        files: dict[str, list],
        **more: Any,
    ) -> dict:
        self.clock += 1000
        entry = {
            "id": f"entry-{len(self.history.get(project_id, [])) + 1}",
            "who": who,
            "label": label,
            "startedAt": self.clock - 500,
            "endedAt": self.clock,
            "files": files,
            "undone": False,
            **more,
        }
        self.history.setdefault(project_id, []).append(entry)
        return entry

    @staticmethod
    def _public(entry: dict) -> dict:
        return {
            "id": entry["id"],
            "who": entry["who"],
            "label": entry["label"],
            "startedAt": entry["startedAt"],
            "endedAt": entry["endedAt"],
            "files": list(entry["files"]),
            "undone": entry["undone"],
            "pinned": False,
        }

    def tool_history_begin(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, folder = self._dir(arguments)
        self.turns[(who, project_id)] = {
            "label": arguments["label"],
            "snapshot": self._snapshot(folder),
        }
        return {"entryId": f"turn-{len(self.history.get(project_id, [])) + 1}"}

    def tool_history_end(self, arguments: dict[str, Any], who: str) -> dict:
        """The entry is every change to the project's files since the turn began, whoever's tool or process wrote them
        (the app sweeps the folder when a window closes), less what the person saved meanwhile."""
        project_id, folder = self._dir(arguments)
        turn = self.turns.pop((who, project_id), None)
        if turn is None:
            return {"entry": None}
        before, now = turn["snapshot"], self._snapshot(folder)
        files = {
            path: [before.get(path), now.get(path)] for path in {*before, *now} if before.get(path) != now.get(path)
        }
        if not files:
            return {"entry": None}
        entry = self._file(project_id, {"kind": "agent", "name": who}, turn["label"], files)
        return {"entry": self._public(entry)}

    def tool_history_list(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, _ = self._dir(arguments)
        entries = self.history.get(project_id, [])
        since = arguments.get("since", "mine")
        if since == "mine":
            own = [n for n, e in enumerate(entries) if e["who"]["name"] == who]
            start = own[-1] if own else 0
        elif any(e["id"] == since for e in entries):
            start = next(n for n, e in enumerate(entries) if e["id"] == since) + 1
        else:
            try:
                time_ms = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp() * 1000
            except ValueError:
                raise ToolRefused(f'since is an entry id, an ISO time or "mine", not "{since}".') from None
            later = [n for n, e in enumerate(entries) if e["endedAt"] > time_ms]
            start = later[0] if later else len(entries)
        shown = entries[start:][::-1][: arguments.get("limit", 20)]
        return {"entries": [self._public(e) for e in shown]}

    def tool_history_undo(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, folder = self._dir(arguments)
        entries = self.history.get(project_id, [])
        mine = [e for e in entries if e["who"]["name"] == who and not e["undone"] and "undoes" not in e]
        if arguments.get("entryId"):
            entry = next((e for e in entries if e["id"] == arguments["entryId"]), None)
            if entry is None:
                raise ToolRefused("That entry is no longer in this project's history.")
            if entry["who"]["name"] != who:
                raise ToolRefused(f"That change is {entry['who']['name']}'s, not yours: put its files back yourself.")
        elif mine:
            entry = mine[-1]
        else:
            raise ToolRefused("You have no change to undo here.")
        now = self._snapshot(folder)
        changed = [path for path, (_, after) in entry["files"].items() if now.get(path) != after]
        if changed:
            newer = [e["id"] for e in entries[entries.index(entry) + 1 :] if set(e["files"]) & set(changed)]
            return {"ok": False, "conflict": {"files": changed, "newer": newer}}
        for path, (before, _) in entry["files"].items():
            if before is None:
                (folder / path).unlink(missing_ok=True)
            else:
                (folder / path).write_bytes(before.encode("utf-8", "surrogateescape"))
        entry["undone"] = True
        undo = self._file(
            project_id,
            {"kind": "agent", "name": who},
            f"Undid: {entry['label']}",
            {p: [after, before] for p, (before, after) in entry["files"].items()},
            undoes=entry["id"],
        )
        return {"ok": True, "entry": self._public(undo)}

    def tool_comments_post(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, _ = self._dir(arguments)
        body, at = arguments["body"], arguments.get("time", 0)
        if not body or len(body) > 4000 or at < 0:
            raise ToolRefused("Invalid comment.")
        threads = self.threads.setdefault(project_id, [])
        number = len(threads) + 1
        self.clock += 1000
        threads.append(
            {
                "id": f"thread-{number}",
                "anchor": {"kind": "moment", "time": at},
                "body": body,
                "status": "open",
                "number": number,
                "createdAt": self.clock,
                "statusAt": self.clock,
                "statusActor": "person",
                "from": who,
            }
        )
        return {
            "threadId": f"thread-{number}",
            "number": number,
            "jobId": f"job-{number}",
            "status": "waiting",
            "note": "Waiting for the person.",
            # the real app puts its token in this link: a bridge must never pass it on
            "browserUrl": f"http://127.0.0.1:{self.port}/?host=1&project={project_id}&t={int(at)}&token={self.token}",
        }

    def tool_comments_list(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, _ = self._dir(arguments)
        status = arguments.get("status", "all")
        threads = [t for t in self.threads.get(project_id, []) if status == "all" or t["status"] == status]
        return {"ok": True, "threads": threads, "notices": []}

    def tool_agent_jobs(self, arguments: dict[str, Any], who: str) -> list:
        project_id, _ = self._dir(arguments)
        return self.jobs.get(project_id, [])

    def tool_comments_resolve(self, arguments: dict[str, Any], who: str) -> dict:
        project_id, _ = self._dir(arguments)
        for thread in self.threads.get(project_id, []):
            if thread["id"] == arguments["threadId"]:
                changed = thread["status"] != arguments["status"]
                thread["status"] = arguments["status"]
                return {"changed": changed}
        raise ToolRefused("No such comment thread.")

    def tool_preview_frame(self, arguments: dict[str, Any], who: str) -> Raw:
        project_id, _ = self._dir(arguments)
        if arguments["time"] < 0:
            raise ToolRefused("Input validation error: time must be at least 0")
        picture = {
            "type": "image",
            "data": base64.b64encode(PNG).decode(),
            "mimeType": "image/png",
        }
        told = {"projectId": project_id, "time": arguments["time"]}
        return Raw([picture, {"type": "text", "text": json.dumps(told)}])
