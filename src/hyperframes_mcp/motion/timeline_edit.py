"""Timeline edits of a composed project by our layer ids (``l-<shot>-<n>``): move, trim, split, duplicate and delete in one
batch, through HyperFrames' own ``timeline apply`` (``hf_cli.apply``: it plans first, writes atomically and leaves a
receipt), never through home-made edits of the page.

The CLI names the layer a split makes ``l-<shot>-<n>-2`` and one a duplicate makes ``l-<shot>-<n>-copy``, and gives it a
block called ``<block>-split``. ``finish_page`` renames them: the next free ``l-<shot>-<n>`` of their shot and the block's
own name, so ``layer_table``, ``run_checks`` and ``replace_media`` read them as any other layer. It also sets the
``durationSeconds`` of each layer whose length changed: a block fills exactly that length (``docs/
motion-library.md``), so a trimmed lower third plays its exit inside its new slot instead of being cut mid-fade.

``plan`` checks the ops and has the CLI plan them (nothing is written); ``apply`` writes. What an edit would change, and
why a plan is refused (a slot shorter than its block needs, a layer pushed past the end of the video), is in ``Summary``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from hyperframes_mcp.hfcli.cli import NODE_DIR, RenderFailed
from hyperframes_mcp.motion import hf_cli, preflight, render
from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion.compose import LayerTiming
from hyperframes_mcp.motion.project_edit import (
    EditError,
    Mount,
    find_tag,
    mounts,
    rewrite_mount,
    set_attributes,
)
from hyperframes_mcp.motion.shotlist import DURATION_VARIABLE, EPS

# the fields each verb takes, besides ``verb`` and ``layer``
VERBS: dict[str, tuple[str, ...]] = {
    "move": ("time",),
    "trim": ("start", "end", "duration"),
    "split": ("time",),
    "duplicate": ("at",),
    "delete": (),
}
# a time as the CLI reads it: seconds, ``+2`` or ``-2`` from the layer's start, frames, or the end of the video
_SECONDS = re.compile(r"\d+(?:\.\d+)?")
_RELATIVE = re.compile(r"[+-]\d+(?:\.\d+)?")
_FRAMES = re.compile(r"\d+(?:\.\d+)?f")
_ANCHOR = re.compile(r"(after|before|start-of|end-of):(.+)")
_LAYER_ID = re.compile(r"l-(.+)-(\d+)")
_LABEL = re.compile(r"shot '([^']*)', layer (\d+) \(([^)]*)\)")
# what the advice after an edit asks run_checks to look at: this many seconds into each changed layer, at most
LOOK_INTO_S = 0.5
MAX_CHECK_TIMES = 8


@dataclass(frozen=True)
class Made:
    """A layer a split or duplicate made; it is kept under the id the CLI gave it."""

    id: str  # ours, l-<shot>-<n>
    origin: str  # the layer it was made from
    component: str  # that layer's block


@dataclass(frozen=True)
class Summary:
    """What an edit changes. ``changes`` is each layer an op named, made or removed, and each one pushed along without being
    named: ``{"layer", "was", "now"}`` (times in seconds; None where the layer was not, or is no more), with ``made_from``
    for a new layer, ``pushed`` for one no op named and ``unchanged`` for a named one that stays where it was. ``made``
    are the new layers by the id the CLI gave them; ``retimed`` the layers whose length is new, as they will be, by our id.
    ``problems`` are why the plan is refused; ``warnings`` are the CLI's and the slot checks'."""

    changes: list[dict[str, Any]]
    made: dict[str, Made]
    retimed: dict[str, LayerTiming]
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def check_at(self) -> list[float]:
        """Seconds worth a look with ``get_frame``: shortly into each layer that now starts somewhere, else where it was."""
        times = {
            round(c["now"][0] + min(LOOK_INTO_S, (c["now"][1] - c["now"][0]) / 2), 2) if c["now"] else c["was"][0]
            for c in self.changes
        }
        return sorted(times)[:MAX_CHECK_TIMES]


@dataclass(frozen=True)
class Planned:
    """Ops the CLI accepted as a plan: what it is sent, and what it would change."""

    cli_ops: list[dict[str, Any]]
    summary: Summary


def cli_ops(page: str, ops: Sequence[dict[str, Any]], length: float) -> list[dict[str, Any]]:
    """The CLI's ops for ``ops`` (``{"verb", "layer", ...}``), after checking each against the layers of ``page`` and the
    ``length`` of the video in seconds.

    Raises:
        EditError: an op names no layer of the page, a verb or field the edit does not take, or a time that is negative,
            past the end of the video or a duration of nothing. Nothing was sent to the CLI.
    """
    if not ops:
        raise EditError("no ops: give at least one move, trim, split, duplicate or delete")
    known = mounts(page)
    return [_cli_op(n, op, known, length) for n, op in enumerate(ops, 1)]


def _cli_op(n: int, op: dict[str, Any], known: dict[str, Mount], length: float) -> dict[str, Any]:
    verb = op.get("verb")
    if verb not in VERBS:
        raise EditError(f"op {n}: verb must be one of {', '.join(VERBS)}, not {verb!r}")
    extra = set(op) - {"verb", "layer", *VERBS[verb]}
    if extra:
        takes = ", ".join(("layer", *VERBS[verb]))
        raise EditError(f"op {n} ({verb}): unknown field {', '.join(sorted(extra))}; it takes {takes}")
    layer = _layer(f"op {n}", op.get("layer"), known)
    where = f"op {n} ({verb} {layer})"
    given = {name: op[name] for name in VERBS[verb] if name in op}
    if VERBS[verb] and not given:
        raise EditError(f"{where}: needs {' or '.join(VERBS[verb])} (seconds)")
    if verb == "trim":
        _check_trim(where, given)
    times = {name: _time(f"{where}, {name}", name, value, known, length) for name, value in given.items()}
    return {"verb": verb, "ref": f"#{layer}", **times}


def _layer(where: str, layer: Any, known: dict[str, Mount]) -> str:
    """``layer`` if the page has it, else an error that names the layers of its shot."""
    if isinstance(layer, str) and layer in known:
        return layer
    match = _LAYER_ID.fullmatch(layer) if isinstance(layer, str) else None
    shot = [i for i in known if match and known[i].layer.shot == match[1]]
    hint = f"the layers of shot {match[1]!r} are {', '.join(shot)}" if shot else "layer_table lists the layers"
    raise EditError(f"{where}: no layer {layer!r} in the page; {hint}")


def _check_trim(where: str, given: dict[str, Any]) -> None:
    """A trim lasts for some time and ends after it starts."""
    duration = given.get("duration")
    if duration is not None and (
        isinstance(duration, bool) or not _SECONDS.fullmatch(str(duration).strip()) or float(duration) <= 0
    ):
        raise EditError(f"{where}: duration must be above 0 seconds, not {duration!r}")
    start, end = given.get("start"), given.get("end")
    numbers = (int, float)
    if isinstance(start, numbers) and isinstance(end, numbers) and end <= start:
        raise EditError(f"{where}: a trim must end after it starts ({start:g} to {end:g})")


def _time(where: str, name: str, value: Any, known: dict[str, Mount], length: float) -> str:
    """``value`` as the CLI reads a time: absolute seconds inside the video, ``+n`` or ``-n`` from the layer's start,
    ``12f``, ``end``, or an anchor on one of our layers (``after:l-p1-1``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise EditError(f"{where}: must be seconds, not {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise EditError(
                f"{where}: {value:g} s is before the start of the video (to move earlier than now, give a string "
                'such as "-2")'
            )
        text = str(value)
    else:
        text = value.strip()
    if _SECONDS.fullmatch(text):
        if float(text) > length + EPS:
            raise EditError(f"{where}: {text} s is past the end of the video ({length:g} s long)")
        return text
    if name == "duration":
        raise EditError(f"{where}: must be seconds above 0, not {value!r}")
    if _RELATIVE.fullmatch(text) or _FRAMES.fullmatch(text) or text == "end":
        return text
    if anchor := _ANCHOR.fullmatch(text):
        return f"{anchor[1]}:#{_layer(where, anchor[2], known)}"
    raise EditError(f"{where}: {value!r} is not seconds (12.5), +2 or -2 from its start, 12f, end, or after:<layer>")


def new_ids(components: dict[str, str], created: Sequence[str]) -> dict[str, Made]:
    """Our id for each layer the CLI made, in the order given: the next free ``l-<shot>-<n>`` of the shot of the layer it
    was made from. ``components`` are the layers there were (id to block). The layer it was made from is what is left of
    the CLI's id when its suffixes (``-2``, ``-copy``) are taken off the end.

    Raises:
        EditError: a layer the CLI made cannot be tied to one that was there.
    """
    taken, made = set(components), {}
    for cli_id in created:
        origin = cli_id
        while origin not in components:
            if origin.count("-") < 3:
                raise EditError(
                    f"HyperFrames made a layer {cli_id!r} that is not tied to a layer of the page; undo the edit"
                )
            origin = origin.rsplit("-", 1)[0]
        shot = re.escape(_LAYER_ID.fullmatch(origin)[1])
        used = [int(m[1]) for i in taken if (m := re.fullmatch(f"l-{shot}-(\\d+)", i))]
        new = f"l-{_LAYER_ID.fullmatch(origin)[1]}-{max(used, default=0) + 1}"
        taken.add(new)
        made[cli_id] = Made(new, origin, components[origin])
    return made


def summarise(
    page: str,
    cli_ops: Sequence[dict[str, Any]],
    result: dict[str, Any],
    length: float,
    registry: registry_module.Registry | None = None,
) -> Summary:
    """What the CLI's plan or receipt (``hf_cli.apply``'s answer to ``cli_ops``) changes in ``page``, the page as it was
    before, in a video ``length`` seconds long."""
    known = mounts(page)
    named = {op["ref"][1:] for op in cli_ops}
    was, now = _rows(result["before"]), _rows(result["after"])
    made = new_ids(
        {i: m.layer.component for i, m in known.items()},
        [i for i in now if i not in known],
    )
    changes: list[dict[str, Any]] = []
    retimed: dict[str, LayerTiming] = {}
    for cli_id, row in now.items():
        ours = made[cli_id].id if cli_id in made else cli_id
        change: dict[str, Any] = {
            "layer": ours,
            "was": _span(was.get(cli_id)),
            "now": _span(row),
        }
        if cli_id in made:
            change["made_from"] = made[cli_id].origin
        elif cli_id not in named:
            change["pushed"] = True
        elif change["was"] == change["now"]:
            change["unchanged"] = True
        changes.append(change)
        if cli_id in made or (cli_id in was and _slot(was[cli_id]) != _slot(row)):
            layer = known[made[cli_id].origin if cli_id in made else cli_id].layer
            retimed[ours] = _timed(layer, ours, row)
    changes += [{"layer": cli_id, "was": _span(row), "now": None} for cli_id, row in was.items() if cli_id not in now]
    changes.sort(key=lambda change: (change["now"] or change["was"])[0])
    problems, warnings = _problems(changes, retimed, length, registry)
    return Summary(changes, made, retimed, problems, [*(result.get("warnings") or []), *warnings])


def _rows(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The top-level layer rows by the id the CLI gives them, in order."""
    return {row["id"]: row for row in rows if str(row.get("id", "")).startswith("l-") and not row.get("nested")}


def _span(row: dict[str, Any] | None) -> list[float] | None:
    return [round(row["start"], 3), round(row["end"], 3)] if row else None


def _slot(row: dict[str, Any]) -> float:
    return round(row["end"] - row["start"], 3)


def _timed(layer: LayerTiming, layer_id: str, row: dict[str, Any]) -> LayerTiming:
    """``layer`` as it will be: under ``layer_id``, at the row's times, its block's ``durationSeconds`` the new length."""
    shot, index = _LAYER_ID.fullmatch(layer_id).groups()
    values = layer.values
    if DURATION_VARIABLE in values:
        values = values | {DURATION_VARIABLE: _slot(row)}
    return replace(
        layer,
        shot=shot,
        index=int(index),
        start=row["start"],
        end=row["end"],
        values=values,
    )


def _problems(
    changes: list[dict[str, Any]],
    retimed: dict[str, LayerTiming],
    length: float,
    registry: registry_module.Registry | None,
) -> tuple[list[str], list[str]]:
    """Why the plan is refused, and what is only worth a warning: a layer past the end of the video, and a slot out of
    its block's range or shorter than the block needs for its text."""
    reg = registry or registry_module.default_registry()
    problems = [
        f"{c['layer']} would end at {c['now'][1]:g} s, after the end of the video ({length:g} s); "
        "shorten it, or move what pushes it"
        for c in changes
        if c["now"] and c["now"][1] > length + EPS
    ]
    for layer_id, layer in retimed.items():
        item = reg.items.get(layer.component)
        limits = {v["id"]: v for v in item.variables}.get(DURATION_VARIABLE, {}) if item else {}
        slot = layer.end - layer.start
        if "min" in limits and slot < limits["min"] - EPS:
            problems.append(
                f"{layer_id} ({layer.component}): a slot of {slot:g} s is shorter than the block's minimum of "
                f"{limits['min']:g} s"
            )
        elif "max" in limits and slot > limits["max"] + EPS:
            problems.append(
                f"{layer_id} ({layer.component}): a slot of {slot:g} s is longer than the block's maximum of "
                f"{limits['max']:g} s"
            )
    warnings: list[str] = []
    found: list[str] = []
    checked = [
        layer for layer in retimed.values() if layer.component in reg.items and DURATION_VARIABLE in layer.values
    ]
    preflight.check_minimum_slots(checked, reg, found, warnings)
    problems += [_LABEL.sub(lambda m: f"l-{m[1]}-{m[2]} ({m[3]})", p) for p in found]
    return problems, warnings


def finish_page(page: str, summary: Summary) -> str:
    """``page`` as HyperFrames wrote it, with each layer a split or duplicate made under our id and its own block's name,
    and the ``durationSeconds`` of each retimed layer its new length."""
    for cli_id, made in summary.made.items():
        if tag := find_tag(page, cli_id):
            page = set_attributes(page, tag, {"id": made.id, "data-composition-id": made.component})
    found = mounts(page)
    for layer_id in summary.retimed:
        mount = found.get(layer_id)
        if mount and DURATION_VARIABLE in mount.layer.values:
            slot = round(mount.layer.end - mount.layer.start, 3)
            values = mount.layer.values | {DURATION_VARIABLE: slot}
            page = rewrite_mount(page, mount, values)
    return page


def plan(project: str | Path, ops: Sequence[dict[str, Any]], *, node_dir: Path = NODE_DIR) -> Planned:
    """Check ``ops`` and have HyperFrames plan them; nothing is written.

    Raises:
        EditError: an op is not valid, or the plan breaks a rule of the library (``Summary.problems``).
        hf_cli.HyperframesError: the CLI refused the plan (an overlap, a split outside the layer), with its reason and fix.
    """
    project = Path(project)
    page = (project / "index.html").read_text(encoding="utf-8")
    length = _video_length(project)
    sent = cli_ops(page, ops, length)
    result = hf_cli.apply(project, sent, dry_run=True, node_dir=node_dir)
    summary = summarise(page, sent, result, length)
    if summary.problems:
        raise EditError("the edit was not made: " + "; ".join(summary.problems))
    return Planned(sent, summary)


def apply(project: str | Path, planned: Planned, *, node_dir: Path = NODE_DIR) -> Summary:
    """Write ``planned`` through the CLI (the page, and a receipt under ``.hyperframes/backup``), and say what it changed.
    The layers it made still carry the CLI's ids and block names: ``finish_page`` renames them on the page it wrote."""
    project = Path(project)
    page = (project / "index.html").read_text(encoding="utf-8")
    result = hf_cli.apply(project, planned.cli_ops, dry_run=False, node_dir=node_dir)
    return summarise(page, planned.cli_ops, result, _video_length(project))


def _video_length(project: Path) -> float:
    try:
        return render.project_duration(project)
    except RenderFailed as error:
        raise EditError(str(error)) from error
