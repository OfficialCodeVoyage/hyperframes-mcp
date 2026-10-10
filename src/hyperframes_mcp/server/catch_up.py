"""What changed in a project since the agent last looked, in few words: the app's history entries and run log, or, with
the app closed, the project's git log and its files' times.

The app's run log (``<project>/.hyperframes/agent-runs.jsonl``) holds one JSON line per run it kept, newest last; a
later line for a run replaces the earlier one. It is written when a run settles, so a run still going is not in it.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

RUN_LOG = Path(".hyperframes") / "agent-runs.jsonl"
MAX_RUNS = 5
MAX_FILES = 20
MAX_COMMITS = 10
LISTED_FILES = 6
TEXT_CHARS = 100
# without a time to go from, the disk route looks back this far
DEFAULT_WINDOW = 24 * 3600.0


def iso(seconds: float) -> str:
    """``seconds`` since the epoch as an ISO time in UTC."""
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="seconds")


def parse_since(since: str | None) -> datetime | None:
    """``since`` as a time when it is an ISO time (UTC when it names no zone), else None (an entry id, ``mine``)."""
    if not since:
        return None
    try:
        when = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def cut(text: str, width: int = TEXT_CHARS) -> str:
    """``text`` on one line, cut to ``width`` characters with an ellipsis."""
    line = " ".join(text.split())
    return line if len(line) <= width else line[: width - 1].rstrip() + "…"


def entries(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The app's history entries as a short list: who, when, what for and which files."""
    return [
        {
            "id": entry["id"],
            "who": entry["who"]["name"],
            "kind": entry["who"]["kind"],
            "at": iso(entry["endedAt"] / 1000),
            "label": cut(entry["label"]),
            "files": entry["files"][:LISTED_FILES],
            "files_more": max(0, len(entry["files"]) - LISTED_FILES),
            "undone": entry["undone"],
        }
        for entry in history
    ]


def agent_runs(root: Path, since: datetime | None) -> list[dict[str, Any]]:
    """The runs of the app's own agent the run log holds, newest first: those settled since ``since``, else the last
    few. An unreadable log or line is skipped; no log gives no runs."""
    try:
        lines = (root / RUN_LOG).read_text("utf-8").splitlines()
    except OSError:
        return []
    jobs: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        job = record.get("job") if record.get("type") == "upsert" else record
        if isinstance(job, dict) and isinstance(job.get("id"), str):
            jobs[job["id"]] = job
    runs = sorted(
        (j for j in jobs.values() if isinstance(j.get("startedAt"), int | float)),
        key=lambda j: j["startedAt"],
        reverse=True,
    )
    if since is not None:
        cutoff = since.timestamp() * 1000
        runs = [j for j in runs if (j.get("endedAt") or j["startedAt"]) >= cutoff]
    return [_run(job) for job in runs[:MAX_RUNS]]


def _run(job: dict[str, Any]) -> dict[str, Any]:
    changed = [f for f in job.get("changedFiles") or [] if isinstance(f, str)]
    return {
        "number": job.get("number"),
        "kind": job.get("kind"),
        "status": job.get("status"),
        "instruction": cut(str(job.get("instruction") or "")),
        "started": iso(job["startedAt"] / 1000),
        "ended": iso(job["endedAt"] / 1000) if job.get("endedAt") else None,
        "changed": changed[:LISTED_FILES],
        "changed_more": max(0, len(changed) - LISTED_FILES),
        "history_entry": job.get("historyEntryId"),
    }


def disk_changes(root: Path, since: datetime | None) -> dict[str, Any]:
    """The disk route, for an app that is closed: the project's git log since ``since`` (when the folder is a git
    repository of its own) and the files written since, newest first. No time given: the last 24 hours."""
    start = since or datetime.now(UTC) - timedelta(seconds=DEFAULT_WINDOW)
    newer = _newer_files(root, start.timestamp())
    return {
        "since": start.isoformat(timespec="seconds"),
        "git": _git_log(root, start),
        "files": [{"path": path, "modified": iso(mtime)} for mtime, path in newer[:MAX_FILES]],
        "files_more": max(0, len(newer) - MAX_FILES),
    }


def _git_log(root: Path, since: datetime) -> list[str] | None:
    """The commits since ``since`` (hash, time, subject), or None when ``root`` is not a git repository of its own."""
    if not (root / ".git").exists():
        return None
    try:
        done = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                f"--since={since.isoformat()}",
                f"-n{MAX_COMMITS}",
                "--pretty=format:%h %cI %s",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return [cut(line, 140) for line in done.stdout.splitlines()]


def _newer_files(root: Path, since: float) -> list[tuple[float, str]]:
    """``(mtime, path)`` of the project's files written since ``since`` (a time in seconds), newest first; hidden
    folders and ``node_modules`` are left out."""
    found: list[tuple[float, str]] = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
        for name in names:
            path = Path(folder) / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime >= since:
                found.append((mtime, path.relative_to(root).as_posix()))
    return sorted(found, reverse=True)
