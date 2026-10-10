"""The bridge to the HyperFrames desktop app's own MCP endpoint: what the person has picked, the history of who changed
what, version-checked file writes, frames, and comments on the timeline.

The app serves a stateless MCP endpoint on its own port (``POST http://127.0.0.1:<port>/mcp``, JSON-RPC ``tools/call``).
It is found each time from the app's two files in ``~/Library/Application Support/HyperFrames/``: ``studio-port.json``
holds the port (the one it saved; if that port was taken at launch the app uses another and does not say so) and
``mcp.json`` holds the bearer token. Both are re-read on every call, so an app that was quit and reopened is found
again. The token is read here and used only as the ``Authorization`` header: it is never logged, never put in a message
or a result (the app's ``browserUrl`` carries it and is dropped), and never copied anywhere.

Requests carry ``User-Agent: hyperframes-mcp/1``: the app files a history entry under the first word of the User-Agent, so
our edits show as ``hyperframes-mcp`` and ``history_undo`` takes back only ours (a word with "claude" or "codex" in it
would be filed under those names).

``AppBridge`` (every method is async)::

    available() -> bool                                          # the app answers a project.list (a busy one is waited for)
    call(tool, arguments, timeout=30) -> Any                     # any tool of the app, its answer as parsed JSON
    project_id(folder) -> str                                    # the app's id for a project folder
    projects() -> list[dict]                                     # Home's projects: id, name, path
    history_begin(project_id, label) -> str                      # open our turn; returns the entry id
    history_end(project_id) -> dict | None                       # close it; the entry, None if nothing changed
    history_list(project_id, since=None, limit=20) -> list[dict] # newest first; since: entry id, ISO time, "mine"
    history_undo(project_id, entry_id=None, mode=None) -> dict   # take back our newest change (or one entry of ours)
    file_read(project_id, path) -> AppFile                       # content and version
    file_write(project_id, path, content, version) -> str        # the new version; AppConflict if stale
    preview_frame(project_id, time) -> bytes                     # a PNG, 960 px wide
    comments_post(project_id, body, time=0.0) -> dict            # a comment on the timeline
    comments_list(project_id, status="all") -> list[dict]
    comments_resolve(project_id, thread_id, resolved=True) -> bool   # mark a comment handled, or open it again
    agent_jobs(project_id) -> list[dict]                         # the app's agent runs: id, kind, status, threadId
    selection(project_id) -> dict | None                         # what the person has picked now

Failures: ``AppUnavailable`` (the app is not running, is signed out, or does not answer: use the disk route; its
subclass ``AppBusy`` says the app took the connection but gave no answer in time, so it is running),
``AppConflict`` (``file_write`` with a version the file has moved on from: read again, redo the change, write again) and
``AppError`` (the app refused: its words, with the token taken out).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# the author our edits are filed under in the app's history (it takes the first word of the User-Agent)
AUTHOR = "hyperframes-mcp"
USER_AGENT = f"{AUTHOR}/1"
SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "HyperFrames"
PORT_FILE = "studio-port.json"
TOKEN_FILE = "mcp.json"
PROBE_TIMEOUT_S = 1.0
# the second try of the probe: right after it has reloaded a page we wrote, the app can be busy for longer than a second
# (a closed app refuses at once, so only a busy one is waited for)
BUSY_TIMEOUT_S = 10.0
CALL_TIMEOUT_S = 30.0
FRAME_TIMEOUT_S = 60.0
# the most projects the app lists in one answer
PAGE = 100
# the app's refusal of a write whose version is stale (main/mcpFileTools.mjs)
CONFLICT_WORDS = "changed since your read"
NOT_RUNNING = "the HyperFrames app is not running, is signed out, or does not answer (its port file may be stale)"


class AppError(RuntimeError):
    """The app refused a call, or answered something unreadable; the message is the app's own words."""


class AppUnavailable(AppError):
    """The app is not running, is signed out, or does not answer: the caller goes the disk route."""


class AppBusy(AppUnavailable):
    """The app took the connection but gave no answer in time: it is running and busy, not closed."""


class AppProjectUnknown(AppError):
    """The app is running but Home does not list this project folder."""


class AppConflict(AppError):
    """``file_write`` with a version the file has moved on from (the person or another agent edited it)."""


@dataclass(frozen=True)
class AppFile:
    """A project file as the app read it; ``version`` is what ``file_write`` must be given back."""

    path: str
    content: str
    version: str


class AppBridge:
    """The app's endpoint, found anew on every call. ``support_dir`` and ``transport`` are for tests: a folder holding
    the two discovery files, and an ``httpx`` transport that stands in for the network."""

    def __init__(
        self,
        support_dir: Path | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._dir = support_dir or SUPPORT_DIR
        self._transport = transport

    # ----- finding the app

    def _endpoint(self) -> tuple[str, str]:
        """The app's base URL and bearer token, from its two files.

        Raises:
            AppUnavailable: a file is missing or unreadable, which is what an app that never ran leaves.
        """
        try:
            port = json.loads((self._dir / PORT_FILE).read_text("utf-8"))["port"]
            token = json.loads((self._dir / TOKEN_FILE).read_text("utf-8"))["token"]
        except (OSError, ValueError, KeyError, TypeError):
            raise AppUnavailable(NOT_RUNNING) from None
        if not (isinstance(port, int) and 1023 < port < 65536):
            raise AppUnavailable(NOT_RUNNING)
        if not (isinstance(token, str) and token):
            raise AppUnavailable(NOT_RUNNING)
        return f"http://127.0.0.1:{port}", token

    async def available(self) -> bool:
        """Whether the app answers a ``project.list``: within a second, or, if it did not, once more within
        ``BUSY_TIMEOUT_S`` (an app that has just reloaded a page we wrote is busy for a moment, not gone; a closed
        app refuses at once and is not waited for)."""
        for limit in (PROBE_TIMEOUT_S, BUSY_TIMEOUT_S):
            try:
                await self.call("project.list", {"limit": 1}, timeout=limit)
            except AppBusy:
                continue
            except AppError:
                return False
            return True
        return False

    # ----- the wire

    async def _send(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float,
        unknown: str | None = None,
    ) -> tuple[Any, str]:
        """One request: the parsed JSON answer and the token (for scrubbing what comes back).

        Raises:
            AppUnavailable: nothing answers, the token is refused, or the app is signed out.
            AppProjectUnknown: the app answered 404 and ``unknown`` says what was not found.
            AppError: any other status, or an answer that is not JSON.
        """
        base, token = self._endpoint()
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/event-stream",
        }
        try:
            # the limit is for the whole call; loopback never goes through a proxy
            async with (
                asyncio.timeout(timeout),
                httpx.AsyncClient(transport=self._transport, timeout=timeout, trust_env=False) as client,
            ):
                response = await client.request(method, base + path, json=body, headers=headers)
        except (httpx.TimeoutException, TimeoutError):
            raise AppBusy(
                "the HyperFrames app did not answer in time (it may be busy loading the project): try again"
            ) from None
        except httpx.TransportError:
            raise AppUnavailable(NOT_RUNNING) from None
        if response.status_code == 401:
            raise AppUnavailable("the app refused the token in its mcp.json (it may have been renewed: try again)")
        if response.status_code == 403:
            raise AppUnavailable(_scrub(_error_message(response) or "the app is signed out", token))
        if response.status_code == 404 and unknown:
            raise AppProjectUnknown(unknown)
        if response.status_code != 200:
            raise AppError(f"the app answered HTTP {response.status_code}")
        try:
            return response.json(), token
        except ValueError:
            raise AppError("the app's answer is not JSON") from None

    async def _content(self, tool: str, arguments: dict[str, Any], timeout: float) -> list[dict[str, Any]]:
        """The content parts of one tool call; a tool's refusal is an ``AppError`` with its words."""
        logger.debug("app tool %s", tool)
        payload, token = await self._send(
            "POST",
            "/mcp",
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            timeout=timeout,
        )
        if "error" in payload:
            message = (payload["error"] or {}).get("message", "")
            raise AppError(_scrub(f"{tool}: {message}", token))
        result = payload.get("result") or {}
        content = result.get("content") or []
        if result.get("isError"):
            text = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
            raise AppError(_scrub(text or f"{tool} was refused", token))
        return [_scrubbed_part(part, token) for part in content]

    async def call(
        self,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = CALL_TIMEOUT_S,
    ) -> Any:
        """One tool of the app's endpoint, its answer parsed from JSON (every app tool but ``preview.frame``).

        Raises:
            AppUnavailable: the app cannot be reached.
            AppError: the tool refused, or answered something that is not JSON.
        """
        content = await self._content(tool, arguments or {}, timeout)
        text = next((p["text"] for p in content if p.get("type") == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            raise AppError(f"{tool} answered something that is not JSON") from None

    async def tool_names(self) -> list[str]:
        """The names of every tool the app's endpoint offers (its ``tools/list``); for ``register --with-app`` and the
        compatibility check, which compare them with the tools known to be free.

        Raises:
            AppUnavailable: the app cannot be reached.
            AppError: the app answered something that is not a tool list.
        """
        payload, token = await self._send(
            "POST",
            "/mcp",
            body={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            timeout=CALL_TIMEOUT_S,
        )
        if "error" in payload:
            raise AppError(_scrub(f"tools/list: {(payload['error'] or {}).get('message', '')}", token))
        tools = (payload.get("result") or {}).get("tools")
        if not isinstance(tools, list):
            raise AppError("the app's tools/list answer has no tools")
        return sorted(tool["name"] for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str))

    # ----- projects

    async def projects(self) -> list[dict[str, Any]]:
        """Home's projects (id, name, path), every page of them."""
        found: list[dict[str, Any]] = []
        while True:
            page = await self.call("project.list", {"offset": len(found), "limit": PAGE})
            found += page
            if len(page) < PAGE:
                return found

    async def project_id(self, folder: str | Path) -> str:
        """The app's id for ``folder``. Home records a project by a link in its own folder, so the links are followed.

        Raises:
            AppProjectUnknown: Home does not list the folder (the person opens it in the app first).
        """
        want = Path(folder).resolve()
        for entry in await self.projects():
            if Path(entry["path"]).resolve() == want:
                return entry["id"]
        raise AppProjectUnknown(f"the HyperFrames app does not list {want}: open this project in the app first")

    # ----- history

    async def history_begin(self, project_id: str, label: str) -> str:
        """Open our turn: every file written until ``history_end`` is one entry of ours, which ``history_undo`` takes
        back alone. Returns the entry id."""
        answer = await self.call("history.begin", {"projectId": project_id, "label": label})
        return answer["entryId"]

    async def history_end(self, project_id: str) -> dict[str, Any] | None:
        """Close our turn: its entry (id, who, label, startedAt, endedAt, files, undone), None if nothing changed."""
        return (await self.call("history.end", {"projectId": project_id}))["entry"]

    async def history_list(self, project_id: str, since: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """What changed and who changed it, newest first. ``since`` is an entry id, an ISO time or ``"mine"`` (the
        default: after our own newest entry, which is everything when we have none)."""
        arguments: dict[str, Any] = {"projectId": project_id, "limit": limit}
        if since:
            arguments["since"] = since
        return (await self.call("history.list", arguments))["entries"]

    async def history_undo(
        self,
        project_id: str,
        entry_id: str | None = None,
        mode: str | None = None,
    ) -> dict[str, Any]:
        """Take back our newest change still in effect (or ``entry_id``, one of ours; someone else's is refused).
        The app's answer: ``{"ok": True, "entry": ...}``, or ``{"ok": False, "conflict": ...}`` when a file has
        changed since, which only the person may override with ``mode`` (``just-this`` or ``back-to-before``)."""
        arguments: dict[str, Any] = {"projectId": project_id}
        if entry_id:
            arguments["entryId"] = entry_id
        if mode:
            arguments["mode"] = mode
        return await self.call("history.undo", arguments)

    # ----- files

    async def file_read(self, project_id: str, path: str) -> AppFile:
        """A project file (``path`` relative to the project, e.g. ``index.html``) and its version."""
        answer = await self.call("file.read", {"projectId": project_id, "path": path})
        return AppFile(answer["path"], answer["content"], answer["version"])

    async def file_write(self, project_id: str, path: str, content: str, version: str | None) -> str:
        """Replace a file, checked against ``version`` (the one ``file_read`` gave; ``None`` creates a new file).
        The app's preview updates and the write is filed in the history under our turn, else as an entry of its own.
        Returns the new version.

        Raises:
            AppConflict: the file changed since ``version``; read it again, redo the change, write again.
        """
        try:
            answer = await self.call(
                "file.write",
                {
                    "projectId": project_id,
                    "path": path,
                    "content": content,
                    "version": version,
                },
            )
        except AppError as error:
            if CONFLICT_WORDS in str(error):
                raise AppConflict(str(error)) from None
            raise
        return answer["version"]

    # ----- the picture, the picks and the comments

    async def preview_frame(self, project_id: str, time: float) -> bytes:
        """The frame at ``time`` seconds as a PNG, rendered from the project on disk now."""
        content = await self._content(
            "preview.frame",
            {"projectId": project_id, "time": time},
            FRAME_TIMEOUT_S,
        )
        image = next((p for p in content if p.get("type") == "image"), None)
        if image is None:
            raise AppError("preview.frame gave no picture")
        return base64.b64decode(image["data"])

    async def selection(self, project_id: str) -> dict[str, Any] | None:
        """What the person has picked in the app now (a snapshot: ``currentTime``, ``target``, ``label``,
        ``textContent``, ...), None when nothing is picked.

        Raises:
            AppProjectUnknown: the app has not opened the project.
        """
        answer, _ = await self._send(
            "GET",
            f"/api/projects/{project_id}/selection",
            timeout=CALL_TIMEOUT_S,
            unknown=f"the app has not opened project {project_id}",
        )
        return answer.get("selection")

    async def comments_post(self, project_id: str, body: str, time: float = 0.0) -> dict[str, Any]:
        """A comment pinned to ``time`` on the person's timeline. It waits in the project's chat until the person
        addresses it. The answer: ``threadId``, ``number``, ``jobId``, ``status``. The link the app adds to it holds
        the app's token, so it is dropped here."""
        answer = await self.call("comments.post", {"projectId": project_id, "body": body, "time": time})
        answer.pop("browserUrl", None)
        return answer

    async def comments_list(self, project_id: str, status: str = "all") -> list[dict[str, Any]]:
        """The project's comment threads (``all``, ``open``, ``in_progress``, ``addressed``, ``declined`` or
        ``resolved``)."""
        answer = await self.call("comments.list", {"projectId": project_id, "status": status})
        if not answer.get("ok"):
            raise AppError(answer.get("error") or "the app could not list the comments")
        return answer["threads"]

    async def agent_jobs(self, project_id: str) -> list[dict[str, Any]]:
        """The app's agent runs on the project (``agent.jobs``), newest first: each has an ``id``, a ``kind`` (a
        comment's Apply run is ``comment``, with the comments it carries as ``threadIds``; builds before b296 had one
        ``threadId``) and a ``status`` (``waiting``, ``queued``, ``running``, ``done``, ``failed`` or ``cancelled``)."""
        answer = await self.call("agent.jobs", {"projectId": project_id})
        if not isinstance(answer, list):
            raise AppError("agent.jobs answered something that is not a list")
        return answer

    async def comments_resolve(self, project_id: str, thread_id: str, resolved: bool = True) -> bool:
        """Mark a comment thread resolved (handled), or open it again; whether its status changed."""
        answer = await self.call(
            "comments.resolve",
            {
                "projectId": project_id,
                "threadId": thread_id,
                "status": "resolved" if resolved else "open",
            },
        )
        return bool(answer.get("changed"))


def _scrub(text: str, token: str) -> str:
    """``text`` without the token."""
    return text.replace(token, "<token>")


def _scrubbed_part(part: dict[str, Any], token: str) -> dict[str, Any]:
    """A content part with the token taken out of its text."""
    if part.get("type") == "text":
        return {**part, "text": _scrub(part.get("text", ""), token)}
    return part


def _error_message(response: httpx.Response) -> str:
    """The message of an error body (JSON-RPC's ``error.message``, or the Studio API's ``error`` text), or ""."""
    try:
        error = response.json()["error"]
        return str(error["message"] if isinstance(error, dict) else error)
    except (ValueError, KeyError, TypeError):
        return ""
