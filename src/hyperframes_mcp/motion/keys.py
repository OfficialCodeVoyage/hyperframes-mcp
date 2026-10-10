"""The API keys of the paid tools, loaded by the code and never shown.

A key is read from the environment first, then from ``~/.hyperframes-mcp/config/.env`` (``KEY=value`` lines), then on
macOS from the Keychain (a generic password whose service is ``hyperframes-mcp`` and whose account is the variable's
name: ``security add-generic-password -s hyperframes-mcp -a FAL_KEY -w``). Nothing is printed and no key is ever
put in a tool's answer.
"""

from __future__ import annotations

import os
import subprocess
import sys

from dotenv import load_dotenv

from hyperframes_mcp.util.paths import user_config_dir

KEY_NAMES = ("OPENAI_API_KEY", "FAL_KEY")
KEYCHAIN_SERVICE = "hyperframes-mcp"
KEYCHAIN_TIMEOUT_S = 5


def _from_keychain(name: str) -> str | None:
    """The Keychain's value for ``name`` (macOS only), or None when there is none or it cannot be read."""
    if sys.platform != "darwin":
        return None
    try:
        done = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def load_keys() -> None:
    """Fill the keys the environment lacks, from the user's ``.env`` and then the Keychain; nothing is printed."""
    env_file = user_config_dir() / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)
    for name in KEY_NAMES:
        if not os.environ.get(name) and (value := _from_keychain(name)):
            os.environ[name] = value
