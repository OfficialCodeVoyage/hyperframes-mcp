"""The server's process setup, done once before the first tool runs."""

from __future__ import annotations

from hyperframes_mcp.motion import keys


def prepare() -> None:
    """Fill the keys the environment lacks (see ``motion.keys``); prints nothing."""
    keys.load_keys()
