"""Helpers of the spend tests: the usual prices, and reading the spend logs of a project."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from hyperframes_mcp.motion import generate

NOW = datetime(2026, 10, 7, 9, 0, 0, tzinfo=UTC)
PICTURE_USD = generate.picture_price("high")
CLIP_USD = generate.clip_price(4)


def spent_lines(project) -> list[dict]:
    """Every line of the project's spend log: the holds as well as the spends."""
    path = project / ".video-mcp" / "spend.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def confirmed_lines(project) -> list[dict]:
    """The spends of the project's log (a hold that was replaced by one is not a second spend)."""
    return [line for line in spent_lines(project) if line.get("status", "confirmed") == "confirmed"]


def earlier_spend(project, usd: float, at: datetime = NOW) -> None:
    """Put ``usd`` in the project's spend log, as if an earlier call had made it."""
    folder = project / ".video-mcp"
    folder.mkdir(exist_ok=True)
    line = json.dumps({"at": at.isoformat(), "usd": usd, "note": "earlier"})
    with (folder / "spend.jsonl").open("a") as log:
        log.write(line + "\n")
