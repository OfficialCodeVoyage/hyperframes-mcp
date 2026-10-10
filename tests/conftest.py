"""Test setup, done before any module of the package is imported.

The pinned node install (HyperFrames CLI, GSAP, d3) is found once from the real home, so a test that moves
``HYPERFRAMES_MCP_HOME`` still finds it; then the home is a throwaway folder, so no test reads the person's settings or
keys or writes to their spend log. CI runs ``hyperframes-mcp setup`` first; a test that needs node or GSAP skips when
they are missing.
"""

import os
import tempfile
from pathlib import Path

from hyperframes_mcp.util.paths import HOME_ENV, NODE_DIR_ENV, node_dir

os.environ.setdefault(NODE_DIR_ENV, str(node_dir()))
os.environ[HOME_ENV] = tempfile.mkdtemp(prefix="hyperframes-mcp-test-home-")

REPO = Path(__file__).resolve().parents[1]
