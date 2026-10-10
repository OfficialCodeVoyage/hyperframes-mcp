"""What the person pointed at in the HyperFrames app, turned into what is on screen there.

The app adds an ``<app_context>`` block to each message of its chat (``main/agent.mjs``, ``main/agentTargetLine.mjs`` of
the app), in words: a picked element ("Apply the instruction to the element at selector "#l-lift-1" ..."), a timeline
range ("... only to the timeline range 61.2s to 70.4s ..."), marks drawn on the frame ("The person drew on the frame ...",
"The person made 2 gestures ..."), or only where the playhead stood ("When the person sent this, the video's playhead
was at 61.62s (01:01.6)"). ``parse_app_context`` reads those into an ``AppContext``; ``context_from_snapshot`` does the
same for the app's ``GET /selection`` answer; ``resolve`` then names the layers on screen at that time or in that
range, the sentence spoken, and the credits shown, from the same page and words ``layer_table`` reads. A pick made on
the timeline carries the playhead's moment, not the layer's, so each picked layer is also given with its own time,
file, credits and narration (``picked_layers``).
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hyperframes_mcp.motion.layer_table import Speech, layer_table, project_words
from hyperframes_mcp.motion.timing import Word

Kind = Literal["pick", "range", "marks", "playhead"]

# the widest a stretch of narration, a layer's text or a mark's words is in a result
NARRATION_CHARS = 400
TEXT_CHARS = 80
MARK_CHARS = 160
# the most layers a result lists; the rest are counted
MAX_LAYERS = 25
# a point in time is the stretch this long, so a layer that starts exactly then is on screen
POINT_S = 0.001

_NUMBER = r"(\d+(?:\.\d+)?)"
_QUOTED = r'("(?:[^"\\]|\\.)*")'
_BLOCK = re.compile(r"<app_context>(.*?)</app_context>", re.DOTALL)
_SELECTOR = re.compile(rf"selector {_QUOTED}")
_LABEL = re.compile(rf"labelled {_QUOTED}")
# a pick's moment, a gesture's moment, and "the video at the moment 12.5s"
_MOMENT = re.compile(rf"\bat {_NUMBER}s(?: \([\d:.]+\))? in the video|\bat the moment {_NUMBER}s")
_PLAYHEAD = re.compile(rf"playhead was at {_NUMBER}s")
# a timeline range, an empty stretch of a track, and the spoken words a stroke crossed
_RANGE = re.compile(rf"timeline range {_NUMBER}s to {_NUMBER}s")
_SLOT = re.compile(rf"timeline track (\d+) \([^)]*\) from {_NUMBER}s to {_NUMBER}s")
_WORDS = re.compile(rf"They play from {_NUMBER}s to {_NUMBER}s in the video")
_TRACKS = re.compile(r"on timeline tracks? ([\d, ]+) \(")
_MARK = re.compile(r"\b(?:circled|pointed at|drew an arrow)\b[^;\n]*")
_ID_SELECTOR = re.compile(r"#([\w-]+)")


class SelectionError(ValueError):
    """The context names no time, or no context was given."""


@dataclass(frozen=True)
class AppContext:
    """What the person pointed at: ``moments`` are times they picked or drew at, ``start``/``end`` a timeline range,
    ``playhead`` where the playhead stood when they sent the message; ``selectors`` and ``labels`` name the elements
    picked, ``marks`` the strokes they drew (in the app's words) and ``tracks`` the timeline rows a range crossed."""

    kind: Kind
    moments: tuple[float, ...] = ()
    start: float | None = None
    end: float | None = None
    playhead: float | None = None
    selectors: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    tracks: tuple[int, ...] = ()
    marks: tuple[str, ...] = ()

    def window(self) -> tuple[float, float] | None:
        """The stretch of the video it is about: the range, else the moments pointed at, else the playhead."""
        if self.start is not None and self.end is not None:
            return self.start, self.end
        if self.moments:
            return min(self.moments), max(self.moments)
        if self.playhead is not None:
            return self.playhead, self.playhead
        return None


def parse_app_context(text: str) -> AppContext:
    """The ``AppContext`` of the block in ``text`` (the last ``<app_context>`` block, else all of ``text``).

    Raises:
        SelectionError: the text names no time.
    """
    blocks = _BLOCK.findall(text)
    block = blocks[-1] if blocks else text
    moments = tuple(dict.fromkeys(float(n) for pair in _MOMENT.findall(block) for n in pair if n))
    playhead = _PLAYHEAD.search(block)
    start = end = None
    tracks: tuple[int, ...] = ()
    if found := _RANGE.search(block) or _WORDS.search(block):
        start, end = float(found.group(1)), float(found.group(2))
    elif found := _SLOT.search(block):
        start, end = float(found.group(2)), float(found.group(3))
        tracks = (int(found.group(1)),)
    if listed := _TRACKS.search(block):
        tracks = tuple(int(n) for n in listed.group(1).split(",") if n.strip())
    marks = tuple(m.group(0).strip()[:MARK_CHARS] for m in _MARK.finditer(block))
    selectors = tuple(json.loads(q) for q in _SELECTOR.findall(block))
    context = AppContext(
        kind=_kind(marks, start, end, selectors),
        moments=moments,
        start=start,
        end=end,
        playhead=float(playhead.group(1)) if playhead else None,
        selectors=selectors,
        labels=tuple(json.loads(q) for q in _LABEL.findall(block)),
        tracks=tracks,
        marks=marks,
    )
    if context.window() is None:
        raise SelectionError("that text names no time: pass the whole <app_context> block of the person's message")
    return context


def _kind(
    marks: tuple[str, ...],
    start: float | None,
    end: float | None,
    selectors: tuple[str, ...],
) -> Kind:
    if marks:
        return "marks"
    if start is not None and end is not None:
        return "range"
    return "pick" if selectors else "playhead"


def context_from_snapshot(snapshot: dict[str, Any]) -> AppContext:
    """The ``AppContext`` of the app's selection snapshot (``GET /selection``): the element picked and the time."""
    target = snapshot.get("target") or {}
    selector = target.get("selector") or (f"#{target['id']}" if target.get("id") else "")
    return AppContext(
        kind="pick" if selector else "playhead",
        moments=(float(snapshot.get("currentTime") or 0.0),),
        selectors=(selector,) if selector else (),
        labels=(snapshot["label"],) if snapshot.get("label") else (),
    )


def resolve(root: Path, context: AppContext) -> dict[str, Any]:
    """What is on screen for ``context`` in the project at ``root``: the layers, the shots, the narration spoken and
    the credits shown. The page is read the way ``layer_table`` reads it; nothing is written."""
    window = context.window()
    if window is None:
        raise SelectionError("that context names no time")
    start, end = window
    point = end - start < POINT_S
    words = project_words(root)
    rows = layer_table(
        root,
        words=words,
        from_s=start,
        to_s=start + POINT_S if point else end,
    )
    picked = [
        {
            "selector": sel,
            "layer": m.group(1) if (m := _ID_SELECTOR.fullmatch(sel)) else None,
        }
        for sel in context.selectors
    ]
    ids = {p["layer"] for p in picked}
    layers = [row for row in rows if row.kind != "credit-line"]
    on_screen = {row.id for row in rows}
    picked_layers = _picked_layers(root, words, ids - {None}, on_screen)
    return {
        "project": str(root),
        "kind": context.kind,
        "at": start if point else None,
        "range": None if point else [start, end],
        "playhead": context.playhead,
        "tracks": list(context.tracks),
        "picked": picked,
        "picked_layers": picked_layers,
        "marks": list(context.marks),
        "layers": [
            {
                "id": row.id,
                "kind": row.kind,
                "start": row.start,
                "end": row.end,
                "file": row.file,
                "text": (row.text or "")[:TEXT_CHARS] or None,
                "credits": [c.id for c in row.credits],
                **({"picked": True} if row.id in ids else {}),
            }
            for row in layers[:MAX_LAYERS]
        ],
        "layers_total": len(layers),
        "shots": list(dict.fromkeys(row.shot for row in rows)),
        "credits": [
            {"id": row.id, "text": row.text, "start": row.start, "end": row.end}
            for row in rows
            if row.kind == "credit-line"
        ],
        **_narration(words, start, end, point),
    }


def _picked_layers(
    root: Path, words: Sequence[Word] | None, ids: set[str], on_screen: set[str]
) -> list[dict[str, Any]]:
    """The layers named by a pick, each with its own time, file, credits and narration. A pick made on the timeline
    carries the playhead's moment, so a picked layer is often not among those on screen there (``on_screen``)."""
    if not ids:
        return []
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "start": row.start,
            "end": row.end,
            "file": row.file,
            "text": (row.text or "")[:TEXT_CHARS] or None,
            "credits": [{"id": c.id, "text": c.text} for c in row.credits],
            "narration": row.narration[:NARRATION_CHARS],
            "on_screen_at_pick": row.id in on_screen,
        }
        for row in layer_table(root, words=words)
        if row.id in ids
    ]


def _narration(words: Sequence[Word] | None, start: float, end: float, point: bool) -> dict[str, Any]:
    """The sentences spoken over a range, whole. At a point, the sentence of the last word begun by then, and whether
    that word is still being said (else the narrator is between sentences or words)."""
    speech = Speech(words) if words else None
    if speech is None:
        return {"narration": "", "speaking": False}
    if not point:
        text = speech.between(start, end)[:NARRATION_CHARS]
        return {"narration": text, "speaking": bool(text)}
    last = bisect_right([word.start for word in speech.words], start) - 1
    if last < 0:
        return {"narration": "", "speaking": False}
    word = speech.words[last]
    text = speech.between(word.start, word.end)[:NARRATION_CHARS]
    return {"narration": text, "speaking": word.end > start}
