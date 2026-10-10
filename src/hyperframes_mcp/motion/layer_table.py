"""The table of a composed project's layers, from its page and its files: what is on screen when, who is credited with
it, whether it is an AI picture, and what the narration says over it (``python -m hyperframes_mcp.motion layers``).

The page (``index.html``) is the truth, because the HyperFrames desktop app edits it: a layer is read by its id and its
composition (``project_edit.mounts``), never by the shot list or the class. The narration comes from the words of the
alignment (``timing.load_alignment``); the caller passes them, ``project_words`` reads them as the project's manifest
names them. Nothing is written, and nothing is called that costs money.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from hyperframes_mcp.motion.compose import COMPONENT_ROWS, LayerTiming
from hyperframes_mcp.motion.manifest import MANIFEST_FILE, read_manifest
from hyperframes_mcp.motion.preflight import credits_of
from hyperframes_mcp.motion.project_edit import MEDIA, Mount, mounts
from hyperframes_mcp.motion.shotlist import CREDIT_BLOCK, EPS
from hyperframes_mcp.motion.timing import Word, load_alignment

Source = Literal["ai", "archival", "graphic"]

# a credit that says the picture is AI-made ("AI-generated", "AI generated")
_AI_CREDIT = re.compile(r"\bai[- ]generated\b", re.IGNORECASE)
# what may follow the mark that ends a sentence: a closing quote or bracket
_CLOSERS = "\"'”’)]"
_SENTENCE_ENDS = (".", "?", "!")
# the widest a cell of the printed table is before it is cut
CELL_WIDTHS = {"what": 40, "credit": 56, "narration": 60}


@dataclass(frozen=True)
class CreditShown:
    """A credit line that belongs to a layer: a picture's credit, or the source line of a graphic."""

    id: str
    text: str


@dataclass(frozen=True)
class LayerRow:
    """One layer of the page.

    ``file`` is the picture or clip of a photo-shot or video-shot, ``text`` the words a block shows, whole;
    ``credits`` are the credit lines that belong to the layer (``_owners``): a picture's credit, a graphic's source line;
    ``source`` is ``ai`` for a picture or clip made by the ``picture`` and ``animate`` commands (its record or its credit
    says so), ``archival`` for any other, ``graphic`` for a block; ``narration`` is what is said over the layer, whole
    sentences, empty without words.
    """

    start: float
    end: float
    id: str
    shot: str
    kind: str
    label: str
    file: str | None
    text: str | None
    credits: list[CreditShown]
    source: Source
    narration: str


class Speech:
    """The narration's words and the sentences they are in, to say what is spoken over a stretch of time.

    A sentence ends at a word that ends in ``.``, ``?`` or ``!`` (a closing quote or bracket may follow it).
    """

    def __init__(self, words: Sequence[Word]) -> None:
        self.words = list(words)
        self._starts = [word.start for word in self.words]
        # the index of the first word of each sentence
        self._firsts = [0]
        for n, word in enumerate(self.words[:-1], 1):
            if word.word.rstrip(_CLOSERS).endswith(_SENTENCE_ENDS):
                self._firsts.append(n)

    def between(self, start: float, end: float) -> str:
        """The sentences with a word said between ``start`` and ``end`` seconds, whole, as one string ("" when no word
        is said then). A word that began before ``start`` and is still being said counts.
        """
        first = bisect_left(self._starts, start)
        while first > 0 and self.words[first - 1].end > start:
            first -= 1
        last = bisect_left(self._starts, end)
        if first >= last:
            return ""
        begin = self._firsts[bisect_right(self._firsts, first) - 1]
        stop = bisect_right(self._firsts, last - 1)
        stop = self._firsts[stop] if stop < len(self._firsts) else len(self.words)
        return " ".join(word.word for word in self.words[begin:stop])


def project_words(project: str | Path) -> list[Word] | None:
    """The words of the narration the project's manifest names, or None when it has no manifest or names none.

    Raises:
        manifest.ManifestError: the manifest cannot be read.
        timing.AlignmentError: the alignment file cannot be read.
    """
    project = Path(project)
    if not (project / MANIFEST_FILE).is_file():
        return None
    alignment = read_manifest(project).alignment
    return load_alignment(project / alignment) if alignment else None


def layer_table(
    project: str | Path,
    *,
    words: Sequence[Word] | None = None,
    from_s: float | None = None,
    to_s: float | None = None,
    kinds: Iterable[str] | None = None,
) -> list[LayerRow]:
    """The layers of ``project``'s page in the order they come on screen.

    ``words`` is the alignment (``timing.load_alignment``; none: no narration). Only the layers on screen for some of
    the time between ``from_s`` and ``to_s``, and of the components named in ``kinds``, are listed.

    Raises:
        OSError: the project has no ``index.html``.
    """
    project = Path(project)
    found = list(mounts((project / "index.html").read_text(encoding="utf-8")).values())
    by_shot: dict[str, list[LayerTiming]] = {}
    for mount in found:
        by_shot.setdefault(mount.layer.shot, []).append(mount.layer)
    owned = {shot: _owners(layers) for shot, layers in by_shot.items()}
    speech = Speech(words) if words else None
    wanted = set(kinds) if kinds is not None else None
    rows = [
        _row(project, mount, owned[mount.layer.shot].get(mount.layer.index, []), speech)
        for mount in found
        if (wanted is None or mount.layer.component in wanted)
        and (from_s is None or mount.layer.end > from_s)
        and (to_s is None or mount.layer.start < to_s)
    ]
    return sorted(rows, key=lambda row: (row.start, row.end))


def _follows(layer: LayerTiming, credit: LayerTiming) -> tuple[bool, float, int]:
    """How well ``credit`` fits ``layer``, lowest first: it starts at or after the layer, soonest, then nearest in the
    shot's list."""
    gap = credit.start - layer.start
    return gap < -EPS, abs(gap), abs(layer.index - credit.index)


def _owners(shot: Sequence[LayerTiming]) -> dict[int, list[LayerTiming]]:
    """The credit lines each layer of one shot owns, by layer index.

    Nothing in the shot list or the page ties a credit line to its layer: both only sit in one shot. So it is read from
    the times, as a shot list is written (a credit starts just after the layer it is for). A picture takes the credit
    line on screen with it (``credits_of``, the preflight's own rule) that fits it best (``_follows``), and so may share
    one. A credit line no picture took is the source line of the graphic it follows, the one that started most recently
    before it; with no graphic to follow it stays with a picture it shares time with, else it belongs to none.
    """
    owned: dict[int, list[LayerTiming]] = {}
    pictures = [layer for layer in shot if layer.component in MEDIA]
    graphics = [layer for layer in shot if layer.component not in (*MEDIA, CREDIT_BLOCK)]
    taken: set[int] = set()
    for picture in pictures:
        if on_screen := credits_of(picture, shot):
            best = min(on_screen, key=lambda credit: _follows(picture, credit))
            owned.setdefault(picture.index, []).append(best)
            taken.add(best.index)
    for credit in shot:
        if credit.component != CREDIT_BLOCK or credit.index in taken or not str(credit.values.get("text", "")).strip():
            continue
        near = [g for g in graphics if not _follows(g, credit)[0] and credit.overlap(g) > EPS] or [
            p for p in pictures if credit.overlap(p) > EPS
        ]
        if near:
            owner = min(near, key=lambda layer: _follows(layer, credit))
            owned.setdefault(owner.index, []).append(credit)
    return owned


def _row(project: Path, mount: Mount, owned: list[LayerTiming], speech: Speech | None) -> LayerRow:
    layer = mount.layer
    media = layer.component in MEDIA
    file = layer.values.get(COMPONENT_ROWS[layer.component][2]) if media else None
    file = file if isinstance(file, str) and file else None
    credits = [CreditShown(f"l-{c.shot}-{c.index}", str(c.values["text"]).strip()) for c in owned]
    return LayerRow(
        start=layer.start,
        end=layer.end,
        id=mount.id,
        shot=layer.shot,
        kind=layer.component,
        label=mount.label,
        file=file,
        text=None if media else _text(layer.component, layer.values),
        credits=credits,
        source=_source(project, file, credits) if media else "graphic",
        narration=speech.between(layer.start, layer.end) if speech else "",
    )


def _text(component: str, values: dict) -> str | None:
    """The words a block shows, whole, on one line: its main variable, else a title, label or text."""
    main = COMPONENT_ROWS.get(component, ("", "", None))[2]
    for name in (main, "title", "label", "text"):
        value = values.get(name) if name else None
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return None


def _source(project: Path, file: str | None, credits: list[CreditShown]) -> Source:
    """``ai`` when the file's record says so or a credit with it says AI-generated, else ``archival``."""
    record = _record(project, file)
    said = [str(record.get("credit") or ""), *(c.text for c in credits)]
    if record.get("ai") is True or any(_AI_CREDIT.search(text) for text in said):
        return "ai"
    return "archival"


def _record(project: Path, file: str | None) -> dict:
    """The file's record (``<file>.json``, written beside it by ``picture``, ``animate`` and ``replace``), or an empty
    one when there is none or it is not a JSON object."""
    if not file:
        return {}
    path = project / file
    try:
        record = json.loads(path.with_name(path.name + ".json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def format_table(rows: Sequence[LayerRow]) -> str:
    """``rows`` as aligned text, one line a row under a header; long cells are cut with an ellipsis."""
    if not rows:
        return "no layers"
    cells = [
        (
            f"{row.start:.2f}",
            f"{row.end:.2f}",
            row.id,
            row.kind,
            row.source,
            _cut(PurePosixPath(row.file).name if row.file else row.text or "", "what"),
            _cut("; ".join(f"{c.id}: {c.text}" for c in row.credits), "credit"),
            _cut(row.narration, "narration"),
        )
        for row in rows
    ]
    header = (
        "START",
        "END",
        "ID",
        "KIND",
        "SOURCE",
        "FILE/TEXT",
        "CREDIT",
        "NARRATION",
    )
    widths = [max(len(line[n]) for line in (header, *cells)) for n in range(len(header))]
    numbers = {0, 1}
    return "\n".join(
        " ".join((cell.rjust if n in numbers else cell.ljust)(widths[n]) for n, cell in enumerate(line)).rstrip()
        for line in (header, *cells)
    )


def _cut(text: str, column: str) -> str:
    width = CELL_WIDTHS[column]
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"
