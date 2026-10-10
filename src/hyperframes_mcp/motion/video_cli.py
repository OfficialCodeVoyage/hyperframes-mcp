"""The shot-list commands of ``python -m hyperframes_mcp.motion``: ``compose``, ``build`` and ``master``.

``compose`` writes the HyperFrames project of a shot list, and with ``--open`` hands it to the HyperFrames desktop app
for a person to edit and render; ``build`` composes it in a scratch
folder, renders it with the pinned HyperFrames (``src/hyperframes_mcp/motion/render.py``) and masters the sound
(``src/hyperframes_mcp/motion/master.py``); ``--check`` on either validates the shot list and prints the resolved timeline without
writing or rendering anything; ``master`` sets the loudness of a video rendered elsewhere (an export from the desktop
app). The narration's word timings are a file you bring (a JSON list of ``{word, start, end}``), made by whatever aligner
you use. Exit code 2 when an input is not valid, 1 when a step fails.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

from hyperframes_mcp.hfcli.cli import NODE_DIR, AnimatedSceneError
from hyperframes_mcp.motion import compose, composing, hf_cli, master, render, sheet, timing
from hyperframes_mcp.motion.shotlist import Master, ShotListError
from hyperframes_mcp.qa.models import UnreadableMediaError

COMMANDS = ("compose", "build", "master")


def add_parsers(sub: argparse._SubParsersAction) -> None:
    """Register the three commands on the CLI's subparsers."""
    for name, help_text, out_help in (
        (
            "compose",
            "write the HyperFrames project of a shot list",
            "the project folder",
        ),
        (
            "build",
            "compose, render and master a shot list into an MP4",
            "the MP4 to write",
        ),
    ):
        parser = sub.add_parser(name, help=help_text)
        parser.add_argument("shotlist", type=Path, help="the shot list JSON")
        parser.add_argument(
            "--alignment",
            type=Path,
            help="the narration's word timings, a JSON list of {word, start, end}",
        )
        parser.add_argument(
            "--assets",
            required=True,
            type=Path,
            help="the folder every path in the shot list is inside",
        )
        parser.add_argument("--out", type=Path, help=out_help)
        parser.add_argument(
            "--profile",
            default="default",
            help="the profile whose style and limits the project follows (default %(default)s)",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="validate and print the resolved timeline; write and render nothing",
        )
        parser.add_argument(
            "--node-dir",
            type=Path,
            default=NODE_DIR,
            help="the folder whose node_modules holds the pinned HyperFrames (default: the one `hyperframes-mcp setup` installs)",
        )
        if name == "compose":
            parser.add_argument(
                "--open",
                action="store_true",
                help="then hand the project to the HyperFrames desktop app; rendering is left to the person",
            )
    mastering = sub.add_parser(
        "master",
        help="set the loudness of a video rendered elsewhere (an export from the HyperFrames desktop app)",
    )
    mastering.add_argument("video", type=Path, help="the video to master")
    mastering.add_argument(
        "--out", required=True, type=Path, help="the mastered MP4 to write"
    )
    mastering.add_argument(
        "--lufs",
        type=float,
        default=Master().lufs,
        help="integrated loudness (default %(default)s, YouTube's level)",
    )
    mastering.add_argument(
        "--true-peak-db",
        type=float,
        default=Master().true_peak_db,
        help="highest true peak in dBTP (default %(default)s)",
    )


def _print_notes(notes: list[str]) -> None:
    for note in notes:
        print(f"note: {note}", file=sys.stderr)


def clip_probe() -> compose.ClipLength | None:
    """A probe of a clip's length (its video stream's), or None with a note on stderr when ffprobe is missing."""
    notes: list[str] = []
    probe = composing.clip_probe(notes)
    _print_notes(notes)
    return probe


def _inputs(args: argparse.Namespace) -> composing.ComposeInputs:
    inputs = composing.load_inputs(args.shotlist, args.alignment, args.assets)
    _print_notes(inputs.notes)
    return inputs


def describe(plan: compose.Plan) -> str:
    """The resolved timeline as text: each shot with its layers, the total and the warnings."""
    lines: list[str] = []
    for shot in plan.shots:
        how = f"dissolve {shot.dissolve_s:g} s" if shot.dissolve_s else "cut"
        lines.append(
            f"shot {shot.id}  {shot.start:.3f} -> {shot.end:.3f} s  ({how} in)"
        )
        lines += [
            f"  layer {layer.index} {layer.component}  {layer.start:.3f} -> {layer.end:.3f} s  slot {layer.slot:g} s"
            for layer in plan.layers
            if layer.shot == shot.id
        ]
    lines.append(f"total {plan.total_s:.3f} s")
    lines += [f"warning: {warning}" for warning in plan.warnings]
    return "\n".join(lines)


def _run_check(args: argparse.Namespace) -> int:
    inputs = _inputs(args)
    checked = compose.plan(
        inputs.shotlist,
        inputs.alignment,
        inputs.voice_duration,
        assets_dir=args.assets,
        clip_seconds=inputs.clip_seconds,
    )
    print(describe(checked))
    print("the shot list is valid")
    return 0


def _run_compose(args: argparse.Namespace) -> int:
    composed = composing.compose_project(
        args.shotlist,
        args.alignment,
        args.out,
        assets=args.assets,
        profile_name=args.profile,
        node_dir=args.node_dir,
    )
    _print_notes(composed.notes)
    for warning in composed.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    result = composed.result
    print(
        f"wrote {len(composed.files)} files to {args.out} ({len(result.shots)} shots, {result.total_s:.3f} s)"
    )
    return _open(args.out, args.node_dir) if args.open else 0


def _open(project: Path, node_dir: Path) -> int:
    """Hand ``project`` to the HyperFrames desktop app; 1 when the app does not open it (the project stays written, and
    the message says why)."""
    try:
        hf_cli.open_project(project, node_dir)
    except hf_cli.HyperframesError as error:
        print(
            f"error: the project is written but the HyperFrames desktop app did not open it: {error}",
            file=sys.stderr,
        )
        return 1
    print(
        f"opened {project} in the HyperFrames desktop app; when the person is back, "
        "`hyperframes catch-up` lists what changed there"
    )
    return 0


def _print_mastered(out: Path, loudness: master.Loudness) -> None:
    print(
        f"mastered {out}: {loudness.integrated_lufs:.1f} LUFS, {loudness.true_peak_dbtp:.1f} dBTP, "
        f"loudness range {loudness.loudness_range_lu:.1f} LU"
    )
    print(f"sha256 {hashlib.sha256(Path(out).read_bytes()).hexdigest()}")


def _run_master(args: argparse.Namespace) -> int:
    try:
        loudness = master.master(
            args.video, args.out, lufs=args.lufs, true_peak_db=args.true_peak_db
        )
    except master.MasterError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _print_mastered(args.out, loudness)
    return 0


def _run_build(args: argparse.Namespace) -> int:
    inputs = _inputs(args)
    shotlist = inputs.shotlist
    with tempfile.TemporaryDirectory(prefix="motion-build-") as scratch_name:
        scratch = Path(scratch_name)
        result = compose.compose(
            shotlist,
            inputs.alignment,
            inputs.voice_duration,
            scratch / "project",
            assets_dir=args.assets,
            clip_seconds=inputs.clip_seconds,
        )
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        try:
            rendered = render.render_project(
                result.project_dir,
                scratch / "raw.mp4",
                node_dir=args.node_dir,
                settings=render.settings_for_length(result.total_s),
            )
            print(
                f"rendered {rendered.width}x{rendered.height} {rendered.fps} fps h264, {rendered.duration_s:.2f} s "
                f"(planned {result.total_s:.2f} s), {rendered.render_seconds:g} s to render ({rendered.isolation} isolation)"
            )
            target = shotlist.audio.master
            loudness = master.master(
                rendered.path,
                args.out,
                lufs=target.lufs,
                true_peak_db=target.true_peak_db,
            )
        except (AnimatedSceneError, sheet.SheetError, master.MasterError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    _print_mastered(args.out, loudness)
    return 0


def run(args: argparse.Namespace) -> int:
    """Run ``compose``, ``build`` or ``master``; returns the exit code."""
    try:
        if args.command == "master":
            return _run_master(args)
        if args.out is None and not args.check:
            print("error: --out is required (or use --check)", file=sys.stderr)
            return 2
        if args.check:
            return _run_check(args)
        return _run_compose(args) if args.command == "compose" else _run_build(args)
    except (
        ShotListError,
        timing.AlignmentError,
        UnreadableMediaError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
