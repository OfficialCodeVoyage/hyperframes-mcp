"""``hyperframes-mcp doctor``: what works on this machine and what does not, one line each, no key or token shown.

Each check returns a ``Check``; none raises and none spends. The app check only asks ``project.list`` (the bridge's
probe); the build is read from the installed ``app.asar`` and compared with the builds this server was tried against.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hyperframes_mcp.hfcli.cli import MIN_NODE_MAJOR, cli_script, node_major, pinned_version
from hyperframes_mcp.motion import keys
from hyperframes_mcp.qa.media import QAToolError, resolve_tools
from hyperframes_mcp.server.appinfo import APP_PATH, KNOWN_APP_TOOLS, TESTED_BUILDS, installed_build
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.util.paths import node_dir

Status = Literal["ok", "warn", "fail"]
CLAUDE_TIMEOUT_S = 20
MIN_PYTHON = (3, 11)


@dataclass(frozen=True)
class Check:
    """One line of the report."""

    name: str
    status: Status
    detail: str


def check_python() -> Check:
    found = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info[:2] >= MIN_PYTHON:
        return Check("python", "ok", found)
    return Check("python", "fail", f"{found}; needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")


def check_node() -> Check:
    node = shutil.which("node")
    if node is None:
        return Check("node", "fail", f"not on PATH; HyperFrames needs Node {MIN_NODE_MAJOR} or newer")
    major = node_major(node)
    if major is None or major < MIN_NODE_MAJOR:
        return Check("node", "fail", f"version {major or 'unknown'}; needs {MIN_NODE_MAJOR} or newer")
    return Check("node", "ok", f"{node} (major {major})")


def check_hyperframes_cli() -> Check:
    pinned = pinned_version()
    script = cli_script(node_dir())
    if not script.is_file():
        return Check("hyperframes cli", "fail", f"not installed (pinned {pinned}): run `hyperframes-mcp setup`")
    try:
        installed = json.loads((script.parents[1] / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return Check("hyperframes cli", "fail", "its package.json is unreadable: run `hyperframes-mcp setup`")
    if installed != pinned:
        return Check("hyperframes cli", "fail", f"{installed} installed, {pinned} pinned: run `hyperframes-mcp setup`")
    return Check("hyperframes cli", "ok", f"{installed} (the pin)")


def check_ffmpeg() -> Check:
    try:
        tools = resolve_tools()
    except QAToolError as error:
        return Check("ffmpeg", "fail", f"{error}; install ffmpeg or set FFMPEG_BIN")
    return Check("ffmpeg", "ok", tools.ffmpeg)


def check_app_build(app: Path = APP_PATH) -> Check:
    if not app.is_dir():
        return Check("app build", "warn", f"{app} not found; the disk route still works, the app link does not")
    build = installed_build(app)
    if build is None:
        return Check("app build", "warn", "the installed app's build could not be read")
    if build in TESTED_BUILDS:
        return Check("app build", "ok", f"{build} (tried: {', '.join(TESTED_BUILDS)})")
    return Check(
        "app build",
        "warn",
        f"{build} was not tried (tried: {', '.join(TESTED_BUILDS)}); run scripts/diff_app_tools.py and see docs/app-compat.md",
    )


def check_app_answers(bridge: AppBridge | None = None) -> Check:
    try:
        answers = asyncio.run((bridge or AppBridge()).available())
    except Exception as error:  # a doctor reports, it does not crash
        return Check("app link", "warn", f"the probe failed: {type(error).__name__}")
    if answers:
        return Check("app link", "ok", "the HyperFrames app answers")
    return Check(
        "app link", "warn", "the app is not running, is signed out, or does not answer; start it and run again"
    )


def check_app_tools(bridge: AppBridge | None = None) -> Check:
    """The tools the app's endpoint offers against the ones reviewed for the tested builds."""
    try:
        offered = set(asyncio.run((bridge or AppBridge()).tool_names()))
    except Exception as error:  # a doctor reports, it does not crash
        return Check("app tools", "warn", f"could not be listed ({type(error).__name__})")
    new, gone = sorted(offered - KNOWN_APP_TOOLS), sorted(KNOWN_APP_TOOLS - offered)
    if new:
        return Check(
            "app tools",
            "warn",
            f"new since the reviewed build: {', '.join(new)}. Read what they do and whether they spend before a chat "
            "uses them (docs/app-compat.md)",
        )
    if gone:
        return Check("app tools", "warn", f"no longer offered: {', '.join(gone)} (this server may call them)")
    return Check("app tools", "ok", f"{len(offered)} tools, all reviewed")


def check_spend() -> Check:
    """Whether paid calls are frozen or held, and which variables are in force."""
    from hyperframes_mcp.motion import spend

    try:
        view = spend.overview()
    except (spend.SpendError, ValueError) as error:
        return Check("spend", "fail", str(error))
    notes = []
    if view["frozen"]:
        notes.append(f"frozen ({view['frozen']})")
    if view["holds"]:
        notes.append(f"{len(view['holds'])} call(s) may have been billed: `hyperframes-mcp spend status`")
    if view["variables_in_force"]:
        notes.append(f"variables in force: {', '.join(view['variables_in_force'])}")
    limits = view["limits"]
    base = f"${limits['per_call_usd']} a call, ${limits['per_project_usd']} a video, {limits['max_calls_per_day']} calls a day"
    return Check("spend", "warn" if view["frozen"] or view["holds"] else "ok", "; ".join([base, *notes]))


def check_keys() -> Check:
    keys.load_keys()
    present = [name for name in keys.KEY_NAMES if os.environ.get(name)]
    if present:
        missing = [name for name in keys.KEY_NAMES if name not in present]
        return Check(
            "keys", "ok", f"set: {', '.join(present)}" + (f"; not set: {', '.join(missing)}" if missing else "")
        )
    return Check(
        "keys",
        "warn",
        f"none of {', '.join(keys.KEY_NAMES)} is set: picture and clip generation is off (free tools work)",
    )


def check_registration(name: str, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> Check:
    """Whether Claude Code has the server and the command it runs exists."""
    if shutil.which("claude") is None:
        return Check("registration", "warn", "the claude command is not on PATH")
    try:
        done = run(
            ["claude", "mcp", "get", name], capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return Check("registration", "warn", "claude mcp get did not answer")
    if done.returncode != 0:
        return Check("registration", "warn", f"{name} is not registered: run `hyperframes-mcp register`")
    command = re.search(r"^\s*Command:\s*(.+)$", done.stdout, re.MULTILINE)
    path = command.group(1).strip() if command else ""
    if not path or not Path(path).exists():
        return Check(
            "registration", "fail", f"{name} runs {path or 'an unknown command'}, which does not exist: register again"
        )
    checkout = re.search(r"PYTHONPATH=(\S+)", done.stdout)
    where = f"{path}" + (f" with PYTHONPATH={checkout.group(1)}" if checkout else "")
    return Check("registration", "ok", f"{name} runs {where}")


def run_checks() -> list[Check]:
    """Every check, in the order they are printed."""
    from hyperframes_mcp.server.server import NAME

    return [
        check_python(),
        check_node(),
        check_hyperframes_cli(),
        check_ffmpeg(),
        check_app_build(),
        check_app_answers(),
        check_app_tools(),
        check_keys(),
        check_spend(),
        check_registration(NAME),
    ]


def report(checks: list[Check]) -> str:
    """The checks as lines, with a last line that says whether anything failed."""
    marks = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}
    lines = [f"{marks[c.status]}  {c.name}: {c.detail}" for c in checks]
    failed = sum(c.status == "fail" for c in checks)
    warned = sum(c.status == "warn" for c in checks)
    lines.append(f"{failed} failed, {warned} to look at" if failed or warned else "all good")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    """1 when a check failed (a warning alone is 0)."""
    return 1 if any(c.status == "fail" for c in checks) else 0
