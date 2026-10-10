"""``hyperframes-mcp setup``: put the pinned HyperFrames CLI and the browser libraries of the block library in place.

The package carries only ``node/package.json`` and its lock. ``npm ci`` installs them into ``user_data_dir()/node``, so
the GSAP library (its own licence, not open source) is fetched from npm by the person who uses it, never shipped here.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from hyperframes_mcp.hfcli.cli import MIN_NODE_MAJOR, cli_script, node_major
from hyperframes_mcp.util.paths import NODE_TEMPLATE_DIR, node_dir

NPM_TIMEOUT_S = 900
TEMPLATE_FILES = ("package.json", "package-lock.json")


class SetupError(RuntimeError):
    """Setup cannot go on; the message says what to install or fix."""


def install(
    target: Path | None = None,
    *,
    log: Callable[[str], None] = print,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> Path:
    """Copy the pinned ``package.json`` and lock to ``target`` and run ``npm ci`` there; returns the CLI's script path.

    Raises:
        SetupError: node is missing or older than ``MIN_NODE_MAJOR``, npm is missing, or ``npm ci`` fails.
    """
    target = target or node_dir()
    node = shutil.which("node")
    major = node_major(node) if node else None
    if major is None or major < MIN_NODE_MAJOR:
        raise SetupError(
            f"HyperFrames needs Node {MIN_NODE_MAJOR} or newer (found: {major or 'none'}); install it first"
        )
    npm = shutil.which("npm")
    if npm is None:
        raise SetupError("npm is not on PATH (it comes with Node)")
    target.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATE_FILES:
        shutil.copy2(NODE_TEMPLATE_DIR / name, target / name)
    log(f"installing the pinned HyperFrames CLI into {target} (npm ci)")
    done = runner(
        [npm, "ci", "--no-audit", "--no-fund"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=NPM_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-5:]
        raise SetupError("npm ci failed: " + " / ".join(tail))
    script = cli_script(target)
    if not script.is_file():
        raise SetupError(f"npm ci finished but {script} is missing")
    return script
