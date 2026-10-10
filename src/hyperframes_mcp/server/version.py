"""Which code this server process runs, so a stale one can be told from a current one.

The HyperFrames app keeps one agent process per chat, and an MCP server's code loads only when that process starts, so
after a code change the chat may hold a server with the old code (or none). ``ping`` and ``project_info`` show what this
process started with: the package version, the commit when it runs from a git checkout (an editable install), when it
started, and its pid. All are read once, when the process starts: a stale process reports what it started with.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

# the folder above ``src/``: a git checkout when the package is installed editable
CHECKOUT = Path(__file__).resolve().parents[3]
GIT_TIMEOUT_S = 5


def package_version() -> str | None:
    """The installed version of ``hyperframes-mcp``, or None when it is not installed (run from source)."""
    try:
        return distribution_version("hyperframes-mcp")
    except PackageNotFoundError:
        return None


def git_sha(repo: Path = CHECKOUT) -> str | None:
    """The short sha of ``repo``'s HEAD, or None when git or the repository is not there."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


STARTED_AT = datetime.now(UTC).isoformat(timespec="seconds")
STARTED_SHA = git_sha()
VERSION = package_version()


def server_version() -> dict[str, Any]:
    """The package version, the commit this process started from (null outside a git checkout), its start time (UTC)
    and its pid."""
    return {"version": VERSION, "git_sha": STARTED_SHA, "started_at": STARTED_AT, "pid": os.getpid()}
