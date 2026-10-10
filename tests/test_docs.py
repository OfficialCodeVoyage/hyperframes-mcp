"""The generated tools page matches the server, and the docs name no command the CLI lacks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_tools_page_is_current():
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_tools_doc.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
