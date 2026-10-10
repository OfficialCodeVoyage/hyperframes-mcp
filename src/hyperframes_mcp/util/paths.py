"""Where the package finds its own files and where it keeps the user's.

- ``PACKAGE_DIR``: the installed package (assets and default config are inside it).
- ``home()``: the user's folder, ``~/.hyperframes-mcp`` unless ``HYPERFRAMES_MCP_HOME`` names another: ``config/`` for
  settings and profiles that replace the bundled ones, ``data/`` for the spend ledger and the pinned HyperFrames CLI.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = PACKAGE_DIR / "assets"
BUNDLED_CONFIG_DIR = PACKAGE_DIR / "config"
# the pinned HyperFrames CLI (package.json and its lock), installed into ``node_dir()`` by ``setup``
NODE_TEMPLATE_DIR = PACKAGE_DIR / "node"
HOME_ENV = "HYPERFRAMES_MCP_HOME"
NODE_DIR_ENV = "HYPERFRAMES_MCP_NODE_DIR"


def home() -> Path:
    """The user's folder of this package (not created)."""
    return Path(os.environ.get(HOME_ENV) or Path.home() / ".hyperframes-mcp")


def user_config_dir() -> Path:
    """Where the user's settings, profiles and keys file live (not created)."""
    return home() / "config"


def user_data_dir() -> Path:
    """Where the package keeps what it writes outside any project: the spend ledger, the CLI install (not created)."""
    return home() / "data"


def node_dir() -> Path:
    """The folder with the pinned HyperFrames CLI in ``node_modules`` (``setup`` puts it in ``user_data_dir()/node``)."""
    return Path(os.environ.get(NODE_DIR_ENV) or user_data_dir() / "node")


def config_path(name: str) -> Path:
    """The settings file ``name``: the user's own when there is one, else the bundled default."""
    own = user_config_dir() / name
    return own if own.is_file() else BUNDLED_CONFIG_DIR / name
