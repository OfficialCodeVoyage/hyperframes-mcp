"""A fake of the pinned HyperFrames CLI for the tools that edit a project's page, installed where ``hf_cli.run`` calls it:
no node and no browser. It answers what the real CLI 0.8.101 printed for a composed project (read from real runs on a
scratch project, 2026-10-07):

- ``timeline apply - --json [--plan]``: ``move``, ``trim``, ``split``, ``duplicate`` and ``delete`` on the page's ``l-...``
  layers, the whole row list before and after, a receipt and a backup when it writes, and the CLI's refusals in its words
  (an overlap, a time outside the video, a split outside the clip, a duration of nothing). A split names the new layer
  ``<id>-2`` and its block ``<block>-split``, a duplicate ``<id>-copy``, and a duplicate pushes the later clips of its
  track on, as the real one does.
- ``history begin|end|undo`` and ``history --json``: one log per project in memory, as the CLI and the app share one on
  disk. An entry is every change to the project's files between begin and end; a change nobody asked for is filed as
  "Outside"; an undo is the author's own newest entry and refuses (a conflict) when a file changed since.

``calls`` holds the arguments of every call. ``refuse_history`` makes ``history`` refuse, as it does while the app has the
project open.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from hyperframes_mcp.motion import project_edit

META = {"version": "0.8.101", "latestVersion": "0.8.138", "updateAvailable": False}
_PLACE = re.compile(r"\d+(?:\.\d+)?")


class Refused(Exception):
    """The CLI refuses: its reason and fix."""

    def __init__(self, reason: str, fix: str) -> None:
        super().__init__(reason)
        self.reason, self.fix = reason, fix


def snapshot(folder: Path) -> dict[str, str]:
    """The files the history keeps (none under a folder that starts with a dot), by path."""
    return {
        path.relative_to(folder).as_posix(): path.read_bytes().decode("utf-8", "surrogateescape")
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(folder).parts)
    }


class History:
    """One project's log."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.entries: list[dict[str, Any]] = []
        self.turn: dict[str, Any] | None = None
        self.tracked = snapshot(folder)
        self.clock = 1_791_000_000_000.0

    def _entry(self, who: dict, label: str, files: dict, **more: Any) -> dict[str, Any]:
        self.clock += 1000
        entry = {
            "id": f"cli-{len(self.entries) + 1}",
            "who": who,
            "label": label,
            "startedAt": self.clock - 500,
            "endedAt": self.clock,
            "files": files,
            "undone": False,
            **more,
        }
        self.entries.append(entry)
        return entry

    @staticmethod
    def public(entry: dict[str, Any]) -> dict[str, Any]:
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

    @staticmethod
    def _changes(before: dict[str, str], now: dict[str, str]) -> dict[str, list]:
        return {
            path: [before.get(path), now.get(path)]
            for path in sorted({*before, *now})
            if before.get(path) != now.get(path)
        }

    def sweep(self) -> None:
        """What changed outside a turn is the outside's; a turn's changes wait for its end."""
        if self.turn is not None:
            return
        now = snapshot(self.folder)
        if changes := self._changes(self.tracked, now):
            outside = {"kind": "outside", "name": "Outside"}
            self._entry(outside, "Changed outside the app", changes)
        self.tracked = now

    def begin(self, who: str, label: str) -> str:
        self.sweep()
        self.turn = {"who": who, "label": label, "snapshot": dict(self.tracked)}
        return f"turn-{len(self.entries) + 1}"

    def end(self) -> dict[str, Any] | None:
        turn, self.turn = self.turn, None
        if turn is None:
            return None
        now = snapshot(self.folder)
        self.tracked = now
        changes = self._changes(turn["snapshot"], now)
        who = {"kind": "agent", "name": turn["who"]}
        return self._entry(who, turn["label"], changes) if changes else None

    def undo(self, who: str) -> dict[str, Any]:
        self.sweep()
        mine = [e for e in self.entries if e["who"]["name"] == who and not e["undone"] and "undoes" not in e]
        if not mine:
            raise Refused(f"{who} has no entry still in effect", "")
        entry, now = mine[-1], snapshot(self.folder)
        stale = [p for p, (_, after) in entry["files"].items() if now.get(p) != after]
        if stale:
            later = self.entries[self.entries.index(entry) + 1 :]
            newer = [e["id"] for e in later if set(e["files"]) & set(stale)]
            return {"ok": False, "conflict": {"files": stale, "newer": newer}}
        for path, (before, _) in entry["files"].items():
            target = self.folder / path
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(before.encode("utf-8", "surrogateescape"))
        entry["undone"] = True
        self.tracked = snapshot(self.folder)
        files = {p: [after, before] for p, (before, after) in entry["files"].items()}
        undo = self._entry(
            {"kind": "agent", "name": who},
            f"Undid: {entry['label']}",
            files,
            undoes=entry["id"],
        )
        return {"ok": True, "entry": self.public(undo)}


class FakeCli:
    """Stands in for ``hf_cli.run``: ``monkeypatch.setattr(hf_cli, "run", FakeCli())``."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.histories: dict[Path, History] = {}
        self.refuse_history: str | None = None
        self.applied = 0

    def history(self, project: Path) -> History:
        return self.histories.setdefault(project.resolve(), History(project.resolve()))

    def __call__(
        self,
        project: Path,
        node_dir: Path,
        *argv: str,
        stdin: str | None = None,
        timeout: float = 0,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        try:
            if argv[:2] == ("timeline", "apply"):
                return self._ok(self._apply(project, json.loads(stdin or "[]"), "--plan" in argv))
            if argv[0] == "history":
                return self._history(project, argv)
        except Refused as refused:
            payload = {"ok": False, "reason": refused.reason, "fix": refused.fix}
            return subprocess.CompletedProcess([], 2, "", json.dumps(payload, indent=2))
        raise AssertionError(f"the fake HyperFrames CLI does not know {argv}")

    @staticmethod
    def _ok(payload: dict[str, Any], code: int = 0) -> subprocess.CompletedProcess[str]:
        text = json.dumps({**payload, "_meta": META}, indent=2)
        return subprocess.CompletedProcess([], code, text, "")

    # ----- history

    def _history(self, project: Path, argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        if self.refuse_history:
            raise Refused(f"history: {self.refuse_history}", "quit the app, or use its Undo")
        log, args = self.history(project), list(argv[1:])
        if args[:1] == ["begin"]:
            who, label = args[args.index("--who") + 1], args[args.index("--label") + 1]
            return self._ok({"entryId": log.begin(who, label)})
        if args[:1] == ["end"]:
            entry = log.end()
            return self._ok(
                {
                    "entry": entry and log.public(entry),
                    "parts": [entry["id"]] if entry else [],
                }
            )
        if args[:1] == ["undo"]:
            try:
                answer = log.undo(args[args.index("--who") + 1])
            except Refused as refused:
                return self._ok({"ok": False, "error": refused.reason}, 2)
            return self._ok(answer, 0 if answer["ok"] else 2)
        log.sweep()
        shown = log.entries[::-1][: int(args[args.index("--limit") + 1])]
        return self._ok({"entries": [log.public(e) for e in shown]})

    # ----- timeline apply

    def _apply(self, project: Path, ops: list[dict[str, Any]], plan: bool) -> dict[str, Any]:
        page_path = project / "index.html"
        page = page_path.read_text("utf-8")
        root = project_edit.find_tag(page, "root")
        length = float(re.search(r'data-duration="([\d.]+)"', root).group(1))
        layers = []
        for match in project_edit._TAG.finditer(page):
            attrs = dict(project_edit._ATTRIBUTE.findall(match.group(0)))
            if attrs.get("id", "").startswith("l-"):
                layers.append(
                    {
                        "id": attrs["id"],
                        "tag": match.group(0),
                        "made": False,
                        "start": float(attrs["data-start"]),
                        "dur": float(attrs["data-duration"]),
                        "track": attrs["data-track-index"],
                        "block": attrs["data-composition-id"],
                    }
                )
        before = [self._row(layer) for layer in layers]
        removed: list[dict[str, Any]] = []
        for op in ops:
            self._op(op, layers, removed, length)
        after = [self._row(layer) for layer in sorted(layers, key=lambda l: l["start"])]
        result: dict[str, Any] = {
            "ok": True,
            "planned": plan,
            "receipt": None,
            "file": ["index.html"],
            "before": before,
            "after": after,
            "diff": "--- before\n+++ after\n",
            "warnings": [],
        }
        if not plan:
            self.applied += 1
            backup = f".hyperframes/backup/2026-10-07T17-20-{self.applied:02d}-index.html"
            (project / backup).parent.mkdir(parents=True, exist_ok=True)
            (project / backup).write_text(page, "utf-8")
            new_page = self._write(page, layers, removed)
            page_path.write_text(new_page, "utf-8")
            result["receipt"] = [
                {
                    "file": "index.html",
                    "version": f'"sha256:{abs(hash(new_page)):x}"',
                    "writeToken": f"token-{self.applied}",
                    "changed": True,
                    "backupPath": backup,
                }
            ]
        return result

    @staticmethod
    def _row(layer: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": layer["id"],
            "label": None,
            "kind": "div",
            "trackKind": "graphics",
            "start": layer["start"],
            "duration": layer["dur"],
            "end": round(layer["start"] + layer["dur"], 6),
            "file": "index.html",
            "trackIndex": int(layer["track"]),
            "ref": f"#{layer['id']}",
            "elementId": layer["id"],
            "hfId": None,
            "nested": False,
        }

    def _op(self, op: dict[str, Any], layers: list, removed: list, length: float) -> None:
        by_id = {layer["id"]: layer for layer in layers}
        name = op["ref"].removeprefix("#")
        if name not in by_id:
            raise Refused(
                f"timeline apply: {op['ref']} was not found",
                'add id="<name>" to the element in its source file, then retry with #<name>',
            )
        layer, verb = by_id[name], op["verb"]

        def when(expression: str) -> float:
            if expression == "end":
                seconds = length
            elif _PLACE.fullmatch(expression):
                seconds = float(expression)
            elif re.fullmatch(r"[+-]\d+(?:\.\d+)?", expression):
                seconds = layer["start"] + float(expression)
            elif anchor := re.fullmatch(r"(after|before|start-of|end-of):#(.+)", expression):
                other = by_id[anchor[2]]
                end = anchor[1] in ("after", "end-of")
                seconds = other["start"] + (other["dur"] if end else 0)
            else:
                raise Refused(f"timeline apply: invalid time expression: {expression}", "")
            if not 0 <= seconds <= length:
                raise Refused(
                    f"timeline apply: time {expression} is outside the composition duration",
                    "pass a time between 0 and the composition duration",
                )
            return seconds

        end = layer["start"] + layer["dur"]
        if verb == "move":
            layer["start"] = when(op["time"])
            self._no_overlap(layer, layers)
        elif verb == "trim":
            start = when(op["start"]) if "start" in op else layer["start"]
            stop = when(op["end"]) if "end" in op else end
            if "duration" in op:
                stop = start + float(op["duration"])
            if stop - start <= 0 or (op.get("duration") and float(op["duration"]) <= 0):
                raise Refused(
                    "timeline apply: trim duration must be positive",
                    "choose a later end or positive duration",
                )
            layer["start"], layer["dur"] = start, stop - start
            self._no_overlap(layer, layers)
        elif verb == "split":
            at = when(op["time"])
            if not layer["start"] < at < end:
                raise Refused(
                    f"timeline apply: {op['ref']} cannot be split at {op['time']}",
                    "choose a time inside the clip",
                )
            taken = {l["id"] for l in layers}
            number = 2
            while f"{name}-{number}" in taken:
                number += 1
            layer["dur"] = at - layer["start"]
            layers.insert(
                layers.index(layer) + 1,
                {
                    **layer,
                    "id": f"{name}-{number}",
                    "made": True,
                    "start": at,
                    "dur": end - at,
                    "block": layer["block"] + "-split",
                    "origin": layer["id"],
                },
            )
        elif verb == "duplicate":
            at = when(op["at"])
            taken = {l["id"] for l in layers}
            copy_id = f"{name}-copy"
            while copy_id in taken:
                copy_id += "-2"
            for other in layers:
                if other is not layer and other["track"] == layer["track"] and other["start"] >= at:
                    other["start"] += layer["dur"]
            layers.insert(
                layers.index(layer) + 1,
                {
                    **layer,
                    "id": copy_id,
                    "made": True,
                    "start": at,
                    "block": layer["block"] + "-split",
                    "origin": layer["id"],
                },
            )
        elif verb == "delete":
            layers.remove(layer)
            removed.append(layer)
        else:
            raise Refused(f"timeline apply: unknown verb {verb}", "")

    @staticmethod
    def _no_overlap(layer: dict[str, Any], layers: list) -> None:
        for other in layers:
            if other is layer or other["track"] != layer["track"]:
                continue
            low = max(layer["start"], other["start"])
            high = min(layer["start"] + layer["dur"], other["start"] + other["dur"])
            if high > low:
                raise Refused(
                    f"timeline apply: #{layer['id']} would overlap #{other['id']} at {low:g}-{high:g}",
                    "pass --overwrite or move the named neighbour",
                )

    @staticmethod
    def _write(page: str, layers: list, removed: list) -> str:
        """The page with each layer's times, the layers made and the layers deleted, as the CLI writes them."""
        for gone in removed:
            page = re.sub(r"[ ]*" + re.escape(gone["tag"]) + r"</div>\n?", "", page, count=1)
        current: dict[str, str] = {}
        for layer in layers:
            changes = {
                "data-start": f"{layer['start']:g}",
                "data-duration": f"{layer['dur']:g}",
            }
            if layer["made"]:
                changes |= {"id": layer["id"], "data-composition-id": layer["block"]}
                origin = current[layer["origin"]]
                tag = project_edit.set_attributes(layer["tag"], layer["tag"], changes)
                page = page.replace(origin + "</div>", origin + "</div>\n      " + tag + "</div>", 1)
            else:
                tag = project_edit.set_attributes(layer["tag"], layer["tag"], changes)
                page = page.replace(layer["tag"], tag, 1)
            current[layer["id"]] = tag
        return page
