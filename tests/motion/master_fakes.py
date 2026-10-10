"""A fake ffmpeg (a shell script) that answers the mastering commands, for the master and CLI tests."""

from __future__ import annotations

import json
import stat
from pathlib import Path

PASS_ONE = {
    "input_i": "-21.50",
    "input_tp": "-4.20",
    "input_lra": "6.10",
    "input_thresh": "-32.00",
    "output_i": "-14.00",
    "output_tp": "-1.00",
    "output_lra": "5.00",
    "output_thresh": "-24.00",
    "normalization_type": "linear",
    "target_offset": "0.04",
}


def summary(i: float, peak: float, lra: float = 5.0) -> str:
    """The end of an ffmpeg ebur128 run, as ffmpeg prints it."""
    return (
        "[Parsed_ebur128_0 @ 0x1] Summary:\n\n  Integrated loudness:\n"
        f"    I:         {i:.1f} LUFS\n    Threshold: -24.0 LUFS\n\n  Loudness range:\n"
        f"    LRA:       {lra:.1f} LU\n    Threshold: -34.0 LUFS\n    LRA low:   -20.0 LUFS\n"
        f"    LRA high:  -15.0 LUFS\n\n  True peak:\n    Peak:      {peak:.1f} dBFS\n"
    )


class FakeFfmpeg:
    """A script that logs its arguments, answers loudnorm and ebur128 from files, and writes the last argument."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.path = folder / "ffmpeg"
        self.log = folder / "calls.log"
        self.path.write_text(
            f"""#!/bin/sh
echo "$@" >> "{self.log}"
case "$*" in
  *print_format=json*) echo "[Parsed_loudnorm_0] " >&2; cat "{folder}/pass1.json" >&2 ;;
  *ebur128*)
    n=$(cat "{folder}/n" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{folder}/n"
    cat "{folder}/measure-$n.txt" >&2 ;;
  *) for last; do :; done; echo mastered > "$last" ;;
esac
""",
            encoding="utf-8",
        )
        self.path.chmod(self.path.stat().st_mode | stat.S_IEXEC)
        self.pass_one(PASS_ONE)

    def pass_one(self, data: dict | str) -> None:
        text = data if isinstance(data, str) else json.dumps(data, indent=4)
        (self.folder / "pass1.json").write_text(text, encoding="utf-8")

    def measures(self, *texts: str) -> None:
        for number, text in enumerate(texts, 1):
            (self.folder / f"measure-{number}.txt").write_text(text, encoding="utf-8")

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()
