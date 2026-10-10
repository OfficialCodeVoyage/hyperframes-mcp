"""Write ``assets/audio/voice.wav``: thirteen seconds of silence standing in for a narration, so the example composes.

    python examples/hello/make_assets.py [ASSETS_DIR]

Replace it with a real recording (and make ``words.json`` match it) to make a video of your own.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

SECONDS = 13
RATE = 24000


def write_voice(assets: Path) -> Path:
    """Write the silent narration under ``assets/audio`` and return its path."""
    out = assets / "audio" / "voice.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"\0\0" * RATE * SECONDS)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "assets"
    print(f"wrote {write_voice(target)}")
