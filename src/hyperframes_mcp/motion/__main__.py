"""Command line of the motion library: ``python -m hyperframes_mcp.motion list|show|lint|install|tokens-css|render|map-data|compose|build|master|layers``.

Everything here is offline and free. ``lint`` exits 1 when it finds a problem, 2 on a bad argument.
``render`` exits 1 when the render fails or the two renders of ``--twice`` differ, 2 on a bad argument.
``compose``, ``build`` and ``master`` (the shot-list commands) are in ``video_cli.py``. ``layers`` (the table of a composed
project's layers, read-only) is here, with the table itself in ``layer_table.py``. Paid picture and clip edits are the
server's tools (``quote_picture``, ``quote_clip``, ``confirm_spend``), not commands.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

from hyperframes_mcp.hfcli.cli import NODE_DIR, AnimatedSceneError
from hyperframes_mcp.motion import (
    layer_table,
    lint,
    manifest,
    maps,
    registry,
    render,
    sheet,
    stage,
    timing,
    tokens,
    video_cli,
)

DEFAULT_OUT_DIR = Path.cwd() / "output" / "motion"


def build_parser() -> argparse.ArgumentParser:
    """The argparse parser of the CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m hyperframes_mcp.motion", description=__doc__.split("\n")[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list the registry items")

    show = sub.add_parser("show", help="show one item: files, variables, source")
    show.add_argument("name")

    check = sub.add_parser(
        "lint",
        help="lint component files or folders (default: the shipped blocks); exit 1 on a problem",
    )
    check.add_argument("paths", nargs="*", metavar="PATH", type=Path)

    install = sub.add_parser(
        "install", help="copy an item into a HyperFrames project directory"
    )
    install.add_argument("name")
    install.add_argument(
        "--dir", required=True, type=Path, help="the HyperFrames project directory"
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="overwrite files changed since the last install",
    )

    sub.add_parser("tokens-css", help="print the tokens.css an install writes")

    draw = sub.add_parser(
        "render",
        help="render one block to an MP4 with the pinned HyperFrames, offline, and look at its frames",
    )
    draw.add_argument("name", help="the block, as `list` names it")
    draw.add_argument(
        "--vars",
        default="",
        metavar="JSON",
        help="a JSON object with the block's variables (default: its defaults)",
    )
    draw.add_argument(
        "--out",
        type=Path,
        default=None,
        help="the MP4 to write (default: output/motion/NAME.mp4 in the current folder)",
    )
    draw.add_argument(
        "--sheet",
        action="store_true",
        help="also write the frames and a labelled contact sheet beside the MP4",
    )
    draw.add_argument(
        "--twice",
        action="store_true",
        help="render again and report whether the two files have the same sha256 (exit 1 if not)",
    )
    draw.add_argument(
        "--node-dir",
        type=Path,
        default=NODE_DIR,
        help="the folder whose node_modules holds the pinned HyperFrames (default: the one `hyperframes-mcp setup` installs)",
    )
    map_data = sub.add_parser(
        "map-data",
        help="build the map-REGION.js data file from Natural Earth GeoJSON files",
    )
    map_data.add_argument("region", choices=sorted(maps.REGIONS))
    map_data.add_argument(
        "--source",
        required=True,
        type=Path,
        help="folder of the Natural Earth GeoJSON files (see assets/motion/data/DATA.md in the package)",
    )
    map_data.add_argument(
        "--out", type=Path, default=maps.DATA_DIR, help="folder to write into"
    )
    layers = sub.add_parser(
        "layers",
        help="list a composed project's layers: when each is on screen, what it is, its credits, and what is said over it",
    )
    layers.add_argument("project", type=Path, help="the composed project folder")
    layers.add_argument(
        "--from",
        dest="from_s",
        type=float,
        metavar="S",
        help="only layers on screen after this second",
    )
    layers.add_argument(
        "--to",
        dest="to_s",
        type=float,
        metavar="S",
        help="only layers on screen before this second",
    )
    layers.add_argument(
        "--kind",
        action="append",
        metavar="K",
        help="only layers of this component, such as photo-shot or credit-line (repeat for more)",
    )
    layers.add_argument(
        "--json", action="store_true", help="print every field as JSON, not a table"
    )
    video_cli.add_parsers(sub)
    return parser


def _list() -> int:
    for item in registry.list_items():
        size = (
            f"{item.dimensions['width']}x{item.dimensions['height']} {item.duration:g}s"
            if item.dimensions
            else "component"
        )
        print(f"{item.name:24} {item.type.split(':')[1]:10} {size:18} {item.title}")
    return 0


def _show(name: str) -> int:
    item = registry.get(name)
    print(f"{item.name} ({item.type}): {item.title}")
    print(item.description)
    print(f"license: {item.license}")
    if item.dimensions:
        print(
            f"canvas: {item.dimensions['width']}x{item.dimensions['height']}, default duration {item.duration:g}s"
        )
    for entry in item.files:
        print(f"file: {entry.path} -> {entry.target} ({entry.type})")
    if item.registry_dependencies:
        print(f"depends on: {', '.join(item.registry_dependencies)}")
    print("variables:")
    for variable in item.variables:
        print(f"  {json.dumps(variable)}")
    if item.source:
        print(f"source: {item.source.url} at {item.source.sha}")
    else:
        print("source: ours")
    if item.notes:
        print(f"notes: {item.notes}")
    return 0


def _lint(paths: list[Path]) -> int:
    problems: list[lint.Problem] = []
    files = 0
    for path in paths or [registry.MOTION_DIR]:
        if path.is_dir():
            files += len(list(path.rglob("*.html")))
            problems += lint.lint_tree(path)
        elif path.is_file():
            files += 1
            problems += lint.lint_file(path)
        else:
            print(f"error: no such file or folder: {path}", file=sys.stderr)
            return 2
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} problem(s) in {files} file(s)")
        return 1
    print(f"{files} file(s) clean")
    return 0


def _install(name: str, directory: Path, force: bool) -> int:
    report = registry.install_item(name, directory, force=force)
    base = directory.resolve()
    for path in report.written:
        print(f"wrote {path.relative_to(base).as_posix()}")
    for path in report.kept:
        print(
            f"kept {path.relative_to(base).as_posix()}: changed since the last install (--force overwrites it)",
            file=sys.stderr,
        )
    print(
        f"{len(report.written)} written, {len(report.kept)} kept, {len(report.unchanged)} already current"
    )
    return 0


def _render(
    name: str,
    raw_vars: str,
    out: Path | None,
    with_sheet: bool,
    twice: bool,
    node_dir: Path,
) -> int:
    try:
        variables = json.loads(raw_vars) if raw_vars else {}
    except json.JSONDecodeError as err:
        print(f"error: --vars is not valid JSON: {err}", file=sys.stderr)
        return 2
    if not isinstance(variables, dict):
        print("error: --vars must be a JSON object", file=sys.stderr)
        return 2
    out = out or DEFAULT_OUT_DIR / f"{name}.mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="motion-stage-") as scratch:
            staged = stage.stage_block(name, variables, Path(scratch) / "project")
            first = render.render_project(staged.project_dir, out, node_dir=node_dir)
            print(
                f"rendered {out}: {first.width}x{first.height} {first.fps} fps h264, "
                f"{first.duration_s:.2f} s, {first.size_bytes / 1000:.1f} KB, "
                f"{first.render_seconds:g} s to render ({first.isolation} isolation)"
            )
            print(f"sha256 {first.sha256}")
            if with_sheet:
                frames, target = sheet.review_sheet(
                    out, staged.duration_s, start=staged.start
                )
                print(f"frames {frames[0].parent}")
                print(f"sheet {target}")
            if twice:
                again = render.render_project(
                    staged.project_dir, Path(scratch) / "again.mp4", node_dir=node_dir
                )
                if again.sha256 != first.sha256:
                    print(f"twice: DIFFERENT, {first.sha256} then {again.sha256}")
                    return 1
                print(f"twice: identical, sha256 {again.sha256}")
    except (AnimatedSceneError, sheet.SheetError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


def _layers(args: argparse.Namespace) -> int:
    """Print the layer table; the narration comes from the alignment the project's manifest names, and without one the
    table has none."""
    try:
        words = layer_table.project_words(args.project)
    except (manifest.ManifestError, timing.AlignmentError) as err:
        print(f"warning: no narration: {err}", file=sys.stderr)
        words = None
    try:
        rows = layer_table.layer_table(
            args.project,
            words=words,
            from_s=args.from_s,
            to_s=args.to_s,
            kinds=args.kind,
        )
    except OSError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([dataclasses.asdict(row) for row in rows], indent=2))
    else:
        print(layer_table.format_table(rows))
    return 0


def _map_data(region: str, source: Path, out: Path) -> int:
    path = maps.write_region(region, source, out)
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the exit code."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            return _list()
        if args.command == "show":
            return _show(args.name)
        if args.command == "lint":
            return _lint(args.paths)
        if args.command == "install":
            return _install(args.name, args.dir, args.force)
        if args.command in video_cli.COMMANDS:
            return video_cli.run(args)
        if args.command == "render":
            return _render(
                args.name, args.vars, args.out, args.sheet, args.twice, args.node_dir
            )
        if args.command == "map-data":
            return _map_data(args.region, args.source, args.out)
        if args.command == "layers":
            return _layers(args)
        print(tokens.stylesheet(), end="")
        return 0
    except (
        registry.RegistryError,
        stage.StageError,
        tokens.TokensError,
        maps.MapDataError,
    ) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
