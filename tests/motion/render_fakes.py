"""Stand-ins for the programs a motion render runs, so no test below needs Chrome, node or ffmpeg.

``fake_toolchain`` writes three shell scripts into a folder and returns a ``Toolchain`` that uses them:

- the HyperFrames CLI (run with ``/bin/sh`` instead of node): ``cli_body`` is its script, with ``$out`` set to the
  path after ``--output`` the way the real CLI takes it;
- ffprobe: prints the JSON in ``probe.json`` beside it;
- ffmpeg: appends its arguments to ``ffmpeg.log`` and copies ``frame.png`` to its last argument, as it would write a frame.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from PIL import Image

from hyperframes_mcp.hfcli.cli import Toolchain
from hyperframes_mcp.qa.models import Tools

TAKE_OUTPUT = 'out=""; while [ $# -gt 0 ]; do [ "$1" = "--output" ] && out="$2"; shift; done\n'
WRITE_CLIP = TAKE_OUTPUT + 'echo frames > "$out"\n'
# a different file every run, so two renders never share a hash
WRITE_VARYING_CLIP = TAKE_OUTPUT + 'echo "$$" > "$out"\n'


def probe_json(
    duration: float = 6.2,
    *,
    width: int = 1920,
    height: int = 1080,
    codec: str = "h264",
    fps: str = "24/1",
) -> dict:
    """What ffprobe says of a finished clip."""
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": width,
                "height": height,
                "avg_frame_rate": fps,
            }
        ],
        "format": {"duration": str(duration)},
    }


def _script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def fake_toolchain(folder: Path, *, cli_body: str = WRITE_CLIP, probe: dict | None = None) -> Toolchain:
    """The three scripts in ``folder`` and a ``Toolchain`` that runs them."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "probe.json").write_text(json.dumps(probe or probe_json()), encoding="utf-8")
    Image.new("RGB", (192, 108), (39, 33, 26)).save(folder / "frame.png")
    ffprobe = _script(folder / "ffprobe", 'cat "$(dirname "$0")/probe.json"\n')
    ffmpeg = _script(
        folder / "ffmpeg",
        'for last; do :; done\necho "$@" >> "$(dirname "$0")/ffmpeg.log"\ncp "$(dirname "$0")/frame.png" "$last"\n',
    )
    cli = _script(folder / "fake-cli.sh", cli_body)
    return Toolchain(
        node="/bin/sh",
        cli=cli,
        browser=folder / "chrome-headless-shell",
        path_dirs=("/usr/bin", "/bin"),
        media=Tools(ffmpeg=str(ffmpeg), ffprobe=str(ffprobe)),
    )


def ffmpeg_calls(folder: Path) -> list[list[str]]:
    """The arguments of every ffmpeg call the fake saw, in order."""
    log = folder / "ffmpeg.log"
    return [line.split() for line in log.read_text().splitlines()] if log.exists() else []
