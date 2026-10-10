"""The checks of a composed project's page that the HyperFrames app's own turn check misses.

The app's check samples a few moments of a video that may be eight minutes long, and can miss the spot that was edited.
This runs, on the page as it is now (``index.html``, which the app edits):

- a picture (a block whose registry entry says ``needsCredit``) with no true credit line on screen with it
  (``preflight.credits_of``, the rule the composer enforces);
- a layer whose slot is shorter than its block needs for its text (a long credit on a short layer: the block throws, so
  nothing of it shows and a render fails; ``preflight.check_minimum_slots``, the composer's own rule);
- a layer hidden behind a layer that covers the frame, a credit line above all (``preflight.check_hidden_layers`` on
  ``project_edit.layers_from_page``, which gives each layer the place the page stacks it in);
- the HyperFrames lint. Its browser ``check`` is not run: a headless Chrome takes over a minute on a long video, and the
  person sees the page in the app (``get_frame`` shows what the app's preview shows).

Every finding is one ``Finding``: its severity, the layer it is about (``l-<shot>-<n>``), the time it is about and one
or two sentences of what is wrong and how to fix it. Nothing is written and nothing is paid.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from hyperframes_mcp.hfcli.cli import NODE_DIR
from hyperframes_mcp.motion import hf_cli, preflight
from hyperframes_mcp.motion import registry as registry_module
from hyperframes_mcp.motion.compose import LayerTiming
from hyperframes_mcp.motion.project_edit import layers_from_page, slot_problems

# a long video can have many findings: the first of them are shown, errors first, and the count of the rest
MAX_FINDINGS = 25
# a finding's sentence is cut to this many characters
MAX_WHAT = 300
# the sections of the CLI's check answer that hold findings
_SECTIONS = ("lint", "runtime", "layout", "motion", "contrast")
_LABEL = re.compile(r"shot '([^']*)', layer (\d+)")
_SHORT = re.compile(r"\blayer (\d+) \(")


@dataclass(frozen=True)
class Finding:
    """One problem: ``layer`` and ``time`` (seconds) say where, None when it is about the page as a whole."""

    severity: str
    layer: str | None
    time: float | None
    what: str


def layer_id(layer: LayerTiming) -> str:
    """The id of the layer's mount on the page."""
    return f"l-{layer.shot}-{layer.index}"


def _named(message: str) -> tuple[str | None, str]:
    """The id of the layer a preflight message starts with, and the message with every layer named by its id."""
    start = _LABEL.match(message)
    if start is None:
        return None, message
    shot = start.group(1)
    message = _LABEL.sub(lambda m: f"l-{shot}-{m.group(2)}", message)
    return f"l-{shot}-{start.group(2)}", _SHORT.sub(lambda m: f"l-{shot}-{m.group(1)} (", message)


def page_findings(page: str, reg: registry_module.Registry | None = None) -> list[Finding]:
    """The credit findings of ``page`` (a picture with none), the slots too short for their text, then its hidden-layer
    errors and warnings."""
    reg = reg or registry_module.default_registry()
    layers = layers_from_page(page)
    by_id = {layer_id(layer): layer for layer in layers}
    found: list[Finding] = []
    for layer in layers:
        item = reg.items.get(layer.component)
        if item and item.needs_credit and not preflight.credits_of(layer, layers):
            found.append(
                Finding(
                    "error",
                    layer_id(layer),
                    layer.start,
                    f"{layer_id(layer)} ({layer.component}) has no credit line with text on screen with it; add a "
                    "credit-line layer in the same shot, on screen while the picture appears, saying where it comes from",
                )
            )
    for message in slot_problems(layers, reg=reg):
        name = message.split(" ", 1)[0]
        layer = by_id.get(name)
        found.append(Finding("error", name, layer.start if layer else None, message))
    problems: list[str] = []
    warnings: list[str] = []
    preflight.check_hidden_layers(layers, reg, problems, warnings)
    for severity, messages in (("error", problems), ("warning", warnings)):
        for message in messages:
            name, what = _named(message)
            layer = by_id.get(name or "")
            found.append(Finding(severity, name, layer.start if layer else None, what))
    return found


def cli_findings(answer: dict[str, Any], layers: Sequence[LayerTiming]) -> list[Finding]:
    """The errors and warnings of a HyperFrames ``check`` answer (its sections of findings), each tied to a layer.

    A finding names a layer by ``elementId`` (lint) or by the file of the block it is in (``compositions/<block>.html``)
    and its time: the layer of that block on screen then, when there is exactly one. Info findings (an entrance or exit
    seen at one moment) are left out, and a finding seen again at another time is shown once.
    """
    found: list[Finding] = []
    seen: set[tuple] = set()
    for section in _SECTIONS:
        for raw in (answer.get(section) or {}).get("findings", []):
            if raw.get("severity") not in ("error", "warning") or _by_design(raw):
                continue
            source = raw.get("sourceFile") or raw.get("file") or ""
            key = (raw.get("code"), raw.get("selector"), raw.get("elementId"), source)
            if key in seen:
                continue
            seen.add(key)
            time = raw.get("time")
            name = raw.get("elementId") or _layer_on_screen(source, time, layers)
            if time is None:
                time = next((layer.start for layer in layers if name == layer_id(layer)), None)
            found.append(Finding(raw["severity"], name, time, _sentence(raw)))
    return found


def _by_design(raw: dict[str, Any]) -> bool:
    """The soft plate behind a block's text (``.scrim``) is larger than its parent on purpose; the check takes it for a
    coordinate mistake (``escaped_container``)."""
    return raw.get("code") == "escaped_container" and str(raw.get("selector", "")).endswith(".scrim")


def _layer_on_screen(source: str, time: float | None, layers: Sequence[LayerTiming]) -> str | None:
    """The id of the one layer of the block in ``source`` that is on screen at ``time``, else None."""
    if time is None:
        return None
    block = PurePosixPath(source).stem
    on_screen = [layer_id(layer) for layer in layers if layer.component == block and layer.start <= time < layer.end]
    return on_screen[0] if len(on_screen) == 1 else None


def _sentence(raw: dict[str, Any]) -> str:
    """``code: message Fix: hint``, cut to ``MAX_WHAT`` characters."""
    text = f"{raw.get('code', 'finding')}: {' '.join(str(raw.get('message', '')).split())}"
    if raw.get("fixHint"):
        text += f" Fix: {' '.join(str(raw['fixHint']).split())}"
    return text if len(text) <= MAX_WHAT else text[: MAX_WHAT - 1].rstrip() + "…"


def run_checks(project: str | Path, *, node_dir: Path = NODE_DIR) -> dict[str, Any]:
    """Check the project's page: its credits and hidden layers, and the HyperFrames lint. Returns ``ok`` (no errors), a
    ``summary`` line, the first ``MAX_FINDINGS`` findings (errors first, then by time), ``more`` (how many are not shown),
    and ``not_run``, the checks that could not run and why (a CLI that is not there does not hide the page's own
    findings).
    """
    project = Path(project)
    page = (project / "index.html").read_text(encoding="utf-8")
    layers = layers_from_page(page)
    findings = page_findings(page)
    not_run: list[str] = []
    try:
        findings += cli_findings({"lint": hf_cli.lint(project, node_dir=node_dir)}, layers)
    except hf_cli.HyperframesError as error:
        not_run.append(f"lint not run: {hf_cli.explained(error, node_dir)}")

    findings.sort(key=lambda f: (f.severity != "error", f.time if f.time is not None else -1.0))
    errors = sum(f.severity == "error" for f in findings)
    warnings = len(findings) - errors
    more = max(0, len(findings) - MAX_FINDINGS)
    shown = [{**asdict(f), "time": None if f.time is None else round(f.time, 2)} for f in findings[:MAX_FINDINGS]]
    return {
        "project": str(project),
        "ok": errors == 0,
        "summary": _summary(errors, warnings, more, len(layers), bool(not_run)),
        "findings": shown,
        "more": more,
        "not_run": not_run,
    }


def _summary(errors: int, warnings: int, more: int, layers: int, unfinished: bool) -> str:
    if errors or warnings:
        line = f"{errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)} in {layers} layers"
        return line + (f"; {more} more not shown" if more else "")
    if unfinished:
        return f"no findings in {layers} layers, but the lint did not run (see not_run)"
    return f"ok: no problems in {layers} layers"
