"""The pinned HyperFrames CLI run on one project: the one place the motion commands and the video MCP server call it.

``node/package.json`` pins the version and ``hyperframes-mcp setup`` installs it (``hfcli.cli.NODE_DIR``). Every call runs with the project folder as its working directory, because the
timeline commands refuse a positional folder, and with the CLI's telemetry, update check and installs off
(``hyperframes_cli.QUIET_ENV``); the rest of this process's environment is kept, since the desktop app's hand-off reads
the agent session from it. A render is not here: ``render.py`` runs it in the hardened sandbox.

The wrappers (``timeline``, ``apply``, ``history_*``, ``check``, ``lint``, ``snapshot``) return the CLI's JSON, without its ``_meta``
(version and update notice), and raise ``HyperframesError`` when the CLI refuses or fails, carrying its reason and fix.
``check`` and ``lint`` exit 1 when they find errors; that is their answer, not a failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from hyperframes_mcp.hfcli.cli import (
    NODE_DIR,
    QUIET_ENV,
    cli_script,
)

TIMEOUT_S = 120
# `check` and `snapshot` start a browser and play the timeline to each time asked
BROWSER_TIMEOUT_S = 300
# what to try when the installed CLI cannot start its browser and gave no fix of its own
BROWSER_FIX = "if the browser is the problem, run `npx hyperframes browser ensure`"


class HyperframesError(Exception):
    """The CLI refused or failed. ``reason`` says why and ``fix`` (when the CLI gave one) what to do about it."""

    def __init__(self, reason: str, fix: str | None = None) -> None:
        super().__init__(f"{reason}; {fix}" if fix else reason)
        self.reason = reason
        self.fix = fix


def run(
    project: Path,
    node_dir: Path,
    *argv: str,
    stdin: str | None = None,
    timeout: float = TIMEOUT_S,
) -> subprocess.CompletedProcess[str]:
    """Run the pinned CLI in ``project`` and return what it printed, whatever its exit code.

    Raises:
        HyperframesError: node or the CLI cannot start, or it runs past ``timeout`` seconds.
    """
    command = [shutil.which("node") or "node", str(cli_script(node_dir)), *argv]
    try:
        return subprocess.run(
            command,
            cwd=project,
            env={**os.environ, **QUIET_ENV, "NO_COLOR": "1"},
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HyperframesError(f"hyperframes {argv[0]} ran past {timeout:g} s and was stopped") from None
    except (OSError, subprocess.SubprocessError) as error:
        raise HyperframesError(str(error)) from error


def version(node_dir: Path = NODE_DIR) -> str | None:
    """The HyperFrames version installed in ``node_dir``, or None when it is not installed there."""
    package = cli_script(node_dir).parents[1] / "package.json"
    try:
        return json.loads(package.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def browser_path(node_dir: Path = NODE_DIR) -> Path:
    """The headless Chrome the CLI renders with (``hyperframes browser path``).

    Raises:
        HyperframesError: the CLI cannot start, or has no browser installed.
    """
    done = run(Path.cwd(), node_dir, "browser", "path")
    lines = done.stdout.strip().splitlines()
    path = Path(lines[-1].strip()) if lines else None
    if path is None or not path.is_file():
        raise HyperframesError("the HyperFrames browser is not installed: run `hyperframes-mcp setup`")
    return path


def timeline(project: Path, *, node_dir: Path = NODE_DIR) -> dict[str, Any]:
    """``hyperframes timeline --json``: ``{"timeline": {"duration", "tracks": [{"kind", "rows": [...]}]}}``.

    A row of a layer we composed has ``id`` = its layer id (``l-<shot>-<n>``), ``ref`` = ``#<id>``, ``start``, ``end``,
    ``trackKind``, ``file`` and ``hfId``.
    """
    return _json(project, node_dir, "timeline", "--json")


def apply(
    project: Path,
    ops: Sequence[dict[str, Any]],
    *,
    dry_run: bool,
    node_dir: Path = NODE_DIR,
) -> dict[str, Any]:
    """Apply timeline edits to the project as one atomic plan (``timeline apply``); ``dry_run`` plans and writes nothing.

    ``ops`` are ``{"verb", "ref", ...}``: verb ``move`` and ``split`` take ``time``, ``trim`` takes ``start``, ``end`` or
    ``duration``, ``duplicate`` takes ``at``, ``set`` takes ``volume``, ``rate`` or ``track``, ``delete`` nothing; a time
    is a string (``"12.5"``, ``"+2"``, ``"after:#l-lift-1"``). The CLI's answer lists every row of the file, hundreds
    of KB on a long video, so ``before`` and ``after`` here hold only the rows an op names by ``#id`` or ``hf:id``, the rows an
    op made or removed (a split or duplicate adds one) and the top-level rows an op moved or resized without naming them
    (a duplicate pushes the later clips of its track on). The rest is the CLI's: ``ok``, ``planned``, ``receipt`` (what
    ``timeline undo`` takes; None on a dry run), ``file``, ``diff`` and ``warnings``.

    Raises:
        HyperframesError: the CLI refused a plan (an unknown ref, a move past the end, an overlap), with its reason and fix.
    """
    argv = ["timeline", "apply", "-", "--json", *(["--plan"] if dry_run else [])]
    result = _json(project, node_dir, *argv, stdin=json.dumps(list(ops)))
    before, after = result.pop("before", []), result.pop("after", [])
    touched = _touched_refs([op["ref"] for op in ops], before, after)
    result["before"] = [row for row in before if row["ref"] in touched]
    result["after"] = [row for row in after if row["ref"] in touched]
    return result


def history_begin(project: Path, who: str, label: str, *, node_dir: Path = NODE_DIR) -> str:
    """``history begin``: ``who``'s writes to the project until ``history_end`` become one history entry under that
    name (the log the desktop app shares). Returns the entry id. The CLI refuses ``history`` while the app has the
    project open.

    Raises:
        HyperframesError: the CLI refused, with its reason.
    """
    return _json(project, node_dir, "history", "begin", "--who", who, "--label", label, "--json")["entryId"]


def history_end(project: Path, *, node_dir: Path = NODE_DIR) -> dict[str, Any] | None:
    """``history end``: the entry of the turn ``history_begin`` opened (id, who, label, files, ...), None when nothing
    was written."""
    return _json(project, node_dir, "history", "end", "--json")["entry"]


def history_undo(project: Path, who: str, *, node_dir: Path = NODE_DIR) -> dict[str, Any]:
    """``history undo``: take back ``who``'s newest entry still in effect, never anyone else's. The answer is
    ``{"ok": True, "entry": ...}``, or ``{"ok": False, "conflict": {"files", "newer"}}`` when a file changed since (an
    answer, not a failure: only the person overrides it).

    Raises:
        HyperframesError: ``who`` has no entry left to undo, or the CLI refused.
    """
    return _json(project, node_dir, "history", "undo", "--who", who, "--json")


def history_list(project: Path, *, limit: int = 20, node_dir: Path = NODE_DIR) -> list[dict[str, Any]]:
    """The newest ``limit`` history entries, newest first (id, who, label, files, undone)."""
    return _json(project, node_dir, "history", "--json", "--limit", str(limit))["entries"]


def check(project: Path, at: Sequence[float] | None = None, *, node_dir: Path = NODE_DIR) -> dict[str, Any]:
    """``hyperframes check --json``: lint, runtime, layout, motion and contrast findings, at ``at`` (seconds) if given."""
    argv = ["check", "--json", *(["--at", _seconds(at)] if at else [])]
    return _json(project, node_dir, *argv, timeout=BROWSER_TIMEOUT_S)


def lint(project: Path, *, node_dir: Path = NODE_DIR) -> dict[str, Any]:
    """``hyperframes lint --json``: ``ok``, the counts and the ``findings`` (exit 1 means it found errors)."""
    return _json(project, node_dir, "lint", "--json")


def snapshot(
    project: Path,
    at: Sequence[float],
    out_dir: Path,
    *,
    node_dir: Path = NODE_DIR,
) -> list[Path]:
    """PNG frames of the composition at each time in ``at`` (seconds), written into ``out_dir``; their paths in order.

    The CLI's optional vision description of the frames (an outside model) is switched off, and only the times asked for
    are captured (no end-of-timeline frame).

    Raises:
        HyperframesError: the CLI fails, or prints no frame.
    """
    if not at:
        raise ValueError("snapshot needs at least one time")
    argv = [
        "snapshot",
        "--at",
        _seconds(at),
        "--no-end",
        "--describe",
        "false",
        "-o",
        str(Path(out_dir).resolve()),
    ]
    done = run(project, node_dir, *argv, timeout=BROWSER_TIMEOUT_S)
    frames = [Path(line.strip()) for line in done.stdout.splitlines() if line.strip().endswith(".png")]
    if done.returncode != 0:
        raise _failure(done, "snapshot", None)
    if not frames:
        raise HyperframesError("hyperframes snapshot printed no frame")
    return frames


def explained(error: HyperframesError, node_dir: Path = NODE_DIR) -> HyperframesError:
    """``error`` for a caller that shows it to an agent: where HyperFrames is not installed (the CLI's own message is the
    last line of a node stack trace) it says so and how to install it, else it keeps the CLI's reason and fix, with the
    browser fix when the CLI gave none."""
    if version(node_dir) is None:
        return HyperframesError(f"HyperFrames is not installed in {node_dir}", "run `npm ci` there")
    return HyperframesError(error.reason, error.fix or BROWSER_FIX)


def open_project(project: Path, node_dir: Path = NODE_DIR) -> None:
    """Hand ``project`` to the HyperFrames desktop app.

    Raises:
        HyperframesError: the app did not open it; the message says why (not installed and where to get it, or the
            CLI's own reason).
    """
    folder = Path(project).resolve()
    opened = run(folder, node_dir, "open", str(folder), "--json")
    if opened.returncode != 0:
        raise HyperframesError(why_not_opened(opened))


def why_not_opened(opened: subprocess.CompletedProcess[str]) -> str:
    """The reason in the CLI's JSON answer (an error, or a reason and where to get the app), else its last line."""
    try:
        answer = json.loads(opened.stdout)
        if "error" in answer:
            return answer["error"]["message"]
        return f"{answer['reason']}; get the app at {answer['downloadUrl']}"
    except (ValueError, KeyError, TypeError):
        lines = (opened.stderr or opened.stdout).strip().splitlines()
        return lines[-1] if lines else "no message"


def _json(
    project: Path,
    node_dir: Path,
    *argv: str,
    stdin: str | None = None,
    timeout: float = TIMEOUT_S,
) -> dict[str, Any]:
    """Run ``argv`` and return its JSON answer without ``_meta``; a refusal or a crash is a ``HyperframesError``."""
    done = run(project, node_dir, *argv, stdin=stdin, timeout=timeout)
    answer = _json_object(done.stdout)
    if answer is None or (answer.get("ok") is False and ("reason" in answer or "error" in answer)):
        raise _failure(done, argv[0], answer)
    answer.pop("_meta", None)
    return answer


def _json_object(text: str) -> dict[str, Any] | None:
    """The JSON object in ``text`` (a log line may come before it), or None."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        answer = json.loads(text[start:])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def _failure(done: subprocess.CompletedProcess[str], name: str, answer: dict[str, Any] | None) -> HyperframesError:
    """The error for a call with no usable answer: the CLI's reason and fix (it prints them as JSON on stderr when it
    refuses), else its error message, else the last line it printed."""
    for payload in (answer, _json_object(done.stderr)):
        if payload is None:
            continue
        if isinstance(payload.get("reason"), str):
            return HyperframesError(payload["reason"], payload.get("fix"))
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return HyperframesError(error["message"])
        if isinstance(error, str) and error:
            return HyperframesError(error)
    lines = (done.stderr or done.stdout).strip().splitlines()
    if done.returncode == 0:
        return HyperframesError(f"hyperframes {name} printed no JSON answer")
    detail = lines[-1] if lines else f"exit status {done.returncode}"
    return HyperframesError(f"hyperframes {name} failed: {detail}")


def _seconds(times: Sequence[float]) -> str:
    """Times for ``--at``: comma-separated seconds to the millisecond, without trailing zeros."""
    return ",".join(f"{t:.3f}".rstrip("0").rstrip(".") for t in times)


def _touched_refs(
    op_refs: Sequence[str],
    before: Sequence[dict[str, Any]],
    after: Sequence[dict[str, Any]],
) -> set[str]:
    """The ``ref`` of each row the ops name (by ``#id`` or ``hf:id``), each a split or duplicate made or a delete removed,
    and each top-level row that moved or changed length (an insert pushes the later clips of its track on)."""
    touched = {row["ref"] for row in before} ^ {row["ref"] for row in after}
    was = {row["ref"]: (row["start"], row["end"]) for row in before}
    touched |= {
        row["ref"]
        for row in after
        if not row.get("nested") and was.get(row["ref"], (row["start"], row["end"])) != (row["start"], row["end"])
    }
    for row in (*before, *after):
        if any(row["ref"] == ref or (ref.startswith("#") and row.get("id") == ref[1:]) for ref in op_refs):
            touched.add(row["ref"])
    return touched
