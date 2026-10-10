"""Command line of hyperframes-mcp: ``hyperframes-mcp serve|register|setup|doctor|spend|adopt|new-chat``.

``serve`` (the default) runs the server over stdio for Claude Code; stdout carries the protocol, so nothing else may
print there. ``register`` adds it to the person's Claude Code at user scope (``claude mcp add --scope user``), the scope
the HyperFrames desktop app's chat loads, running the installed command. ``setup`` installs the pinned HyperFrames CLI.
``doctor`` says what works. ``spend`` shows the spend of every project, freezes or thaws paid calls, and clears a call
that may have been billed. ``adopt`` gives a project composed before the manifest existed its ``video.project.json``.
``new-chat`` gives a project's chat in the HyperFrames app a fresh start (the app has no button for it).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from hyperframes_mcp.server import env

# where the app keeps the chat of a project: every run of the chat names its session, and the next message resumes it
RUNS_FILE = Path(".hyperframes") / "agent-runs.jsonl"
APP_PROCESS = "HyperFrames.app/Contents/MacOS/"
APP_SERVER_NAME = "hyperframes-desktop"
CLAUDE_TIMEOUT_S = 60


def _package_version() -> str:
    """The installed version of the package (``unknown`` when it runs from a source tree that was never installed)."""
    try:
        return metadata.version("hyperframes-mcp")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    """The argparse parser of the CLI."""
    parser = argparse.ArgumentParser(prog="hyperframes-mcp", description=__doc__.split("\n")[0])
    parser.add_argument("--version", action="version", version=f"hyperframes-mcp {_package_version()}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the server over stdio (the default)")
    register = sub.add_parser(
        "register",
        help="add the server to Claude Code at user scope, replacing an earlier entry of the same name",
    )
    register.add_argument(
        "--dev",
        type=Path,
        metavar="CHECKOUT",
        help="run the code of this checkout (its src/ folder goes on PYTHONPATH) instead of the installed package",
    )
    register.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "set a variable of the server in the registered entry, for example HYPERFRAMES_MCP_PER_PROJECT_USD=5 "
            "(a spend variable can only lower a limit); repeatable"
        ),
    )
    register.add_argument(
        "--with-app",
        action="store_true",
        help=(
            "also add the HyperFrames app's own server (hyperframes-desktop), which gives every chat the app's tools, "
            "its media finders included; the app's token goes into Claude Code's config"
        ),
    )
    spend = sub.add_parser(
        "spend",
        help="the spend of every project; freeze or thaw paid calls; clear a call that may have been billed",
    )
    spend_sub = spend.add_subparsers(dest="spend_command", required=True)
    spend_sub.add_parser(
        "status", help="limits in force, calls and dollars today, and the calls that may have been billed"
    )
    spend_sub.add_parser("freeze", help="refuse every paid call until thawed (quoting still works)")
    spend_sub.add_parser("thaw", help="allow paid calls again")
    clear = spend_sub.add_parser(
        "clear", help="answer for a call that may have been billed, after checking the provider's dashboard"
    )
    clear.add_argument("quote", help="the quote id (spend status lists them)")
    answer = clear.add_mutually_exclusive_group(required=True)
    answer.add_argument("--billed", action="store_true", help="it was billed: keep its price as spent")
    answer.add_argument("--not-billed", action="store_true", help="it was not billed: release its price")
    sub.add_parser(
        "setup", help="install the pinned HyperFrames CLI and the block library's browser libraries (npm ci)"
    )
    sub.add_parser("doctor", help="check what works on this machine; shows no key or token")
    adopt = sub.add_parser(
        "adopt",
        help="write the manifest (video.project.json) of a project composed before compose wrote one",
    )
    adopt.add_argument("project", type=Path, help="the composed project folder")
    adopt.add_argument("--profile", default="default", help="the profile whose style and limits the project follows")
    adopt.add_argument("--topic", type=int, help="an id of the video in a host program's ledger")
    adopt.add_argument("--shotlist", type=Path, help="the shot list it was composed from")
    adopt.add_argument("--alignment", type=Path, help="the narration's word timings (words.json)")
    adopt.add_argument("--assets", type=Path, help="the assets folder it was composed with")
    adopt.add_argument("--voice", type=Path, help="the narration audio")
    adopt.add_argument("--force", action="store_true", help="replace a manifest the project already has")
    new_chat = sub.add_parser(
        "new-chat",
        help="give a project's chat in the HyperFrames app a fresh start (quit the app first)",
    )
    new_chat.add_argument("project", type=Path, help="the project folder")
    return parser


def _variables_of_the_server() -> set[str]:
    """The names ``register --env`` accepts: the spend variables and the folders; nothing else reaches the entry."""
    from hyperframes_mcp.motion import spend
    from hyperframes_mcp.util.paths import HOME_ENV, NODE_DIR_ENV

    return {name for name, _ in spend.VARIABLES.values()} | {spend.FREEZE_VARIABLE, HOME_ENV, NODE_DIR_ENV}


def register_command(python: str = sys.executable, dev: Path | None = None, env: tuple[str, ...] = ()) -> list[str]:
    """The ``claude mcp add`` command that registers this server for every session of the person.

    It runs ``python -m hyperframes_mcp serve`` with the Python that installed the package, so the entry keeps working
    when the command is not on PATH; ``dev`` adds a checkout's ``src/`` to ``PYTHONPATH`` for development; ``env`` are
    ``NAME=VALUE`` variables of the server.

    Raises:
        ValueError: a variable is not ``NAME=VALUE`` of a name the server reads.
    """
    from hyperframes_mcp.server.server import NAME

    allowed = _variables_of_the_server()
    command = ["claude", "mcp", "add", "--scope", "user", "--transport", "stdio", NAME]
    for item in env:
        name, _, value = item.partition("=")
        if name not in allowed or not value:
            raise ValueError(f"--env takes NAME=VALUE with a name of {', '.join(sorted(allowed))}; got {item!r}")
        command += ["-e", item]
    if dev is not None:
        command += ["-e", f"PYTHONPATH={dev.expanduser().resolve() / 'src'}"]
    return command + ["--", python, "-m", "hyperframes_mcp", "serve"]


def _claude(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["claude", *argv], capture_output=True, text=True, timeout=CLAUDE_TIMEOUT_S, check=False)


def _app_server_command() -> list[str] | None:
    """The ``claude mcp add`` command for the app's own server, built from its two discovery files; None when the app
    has never run. The token is in the URL, as the app's own Connect line has it, and is never printed."""
    from hyperframes_mcp.server.bridge import AppBridge, AppUnavailable

    try:
        base, token = AppBridge()._endpoint()
    except AppUnavailable:
        return None
    return ["mcp", "add", "--scope", "user", "--transport", "http", APP_SERVER_NAME, f"{base}/mcp?token={token}"]


def _register(args: argparse.Namespace) -> int:
    from hyperframes_mcp.server.server import NAME

    if shutil.which("claude") is None:
        print("error: the claude command is not on PATH", file=sys.stderr)
        return 1
    _claude("mcp", "remove", "--scope", "user", NAME)
    try:
        command = register_command(dev=args.dev, env=tuple(args.env))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    added = subprocess.run(command, capture_output=True, text=True, check=False)
    if added.returncode != 0:
        print(f"error: claude mcp add failed: {(added.stderr or added.stdout).strip()}", file=sys.stderr)
        return 1
    where = f"{sys.executable} -m hyperframes_mcp serve" + (
        f", PYTHONPATH={args.dev.expanduser().resolve() / 'src'}" if args.dev else ""
    )
    print(f"registered {NAME} at user scope: {where}")
    if args.with_app:
        command = _app_server_command()
        if command is None:
            print(
                "not added: the HyperFrames app has not run on this machine (start it, then run this again)",
                file=sys.stderr,
            )
            return 1
        _claude("mcp", "remove", "--scope", "user", APP_SERVER_NAME)
        done = _claude(*command)
        if done.returncode != 0:
            print("error: claude mcp add of the app's server failed", file=sys.stderr)  # its output may carry the URL
            return 1
        print(
            f"registered {APP_SERVER_NAME} (the app's own server); its port can change when the app restarts: register again then"
        )
    print("quit the HyperFrames app completely (Cmd+Q) and reopen it: a chat loads its servers only when it starts")
    return 0


def _spend(args: argparse.Namespace) -> int:
    from hyperframes_mcp.motion import spend

    try:
        if args.spend_command == "freeze":
            print(f"frozen: no paid call is made until `hyperframes-mcp spend thaw` ({spend.freeze()})")
        elif args.spend_command == "thaw":
            print("thawed: paid calls are allowed again" if spend.thaw() else "it was not frozen")
        elif args.spend_command == "clear":
            done = spend.clear_hold(args.quote, billed=args.billed)
            kept = (
                f"kept as spent, ${done['usd']:.2f}"
                if done["billed"]
                else f"released, ${done['usd']:.2f} counts no more"
            )
            print(f"{done['quote_id']} ({done['project']}): {kept}")
        else:
            view = spend.overview()
            limits = view["limits"]
            print(f"frozen: {view['frozen']}" if view["frozen"] else "paid calls: allowed")
            print(f"data folder: {view['data_folder']} (the server must use the same one: HYPERFRAMES_MCP_HOME)")
            if view["damaged_lines"]:
                print(
                    f"WARNING: {view['damaged_lines']} line(s) of the spend log cannot be read; nothing can be quoted "
                    f"or confirmed until they are mended or removed ({view['data_folder']}/spend.jsonl)"
                )
            print(
                f"today: ${view['spent_today_usd']:.2f} of ${limits['all_projects_per_day_usd']} in {view['calls_today']} "
                f"calls (max {limits['max_calls_per_day']}); last hour: {view['calls_last_hour']} calls "
                f"(max {limits['max_calls_per_hour']})"
            )
            forced = view["variables_in_force"]
            print(
                f"per call ${limits['per_call_usd']}, per video ${limits['per_project_usd']}"
                + (f"; variables in force: {', '.join(forced)}" if forced else "")
            )
            for hold in view["holds"]:
                print(
                    f"hold {hold['quote']} ${hold['usd']:.2f} since {hold['at']} ({hold['status']}) in {hold['project']}: "
                    f"`hyperframes-mcp spend clear {hold['quote']} --billed` or `--not-billed`"
                )
            if not view["holds"]:
                print("no call is held")
    except (spend.SpendError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _setup() -> int:
    from hyperframes_mcp.hfcli.install import SetupError, install

    try:
        script = install()
    except SetupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"installed: {script}")
    return 0


def _doctor() -> int:
    from hyperframes_mcp.server import doctor

    checks = doctor.run_checks()
    print(doctor.report(checks))
    return doctor.exit_code(checks)


def _adopt(args: argparse.Namespace) -> int:
    from hyperframes_mcp.motion.manifest import (
        MANIFEST_FILE,
        ProjectManifest,
        file_sha256,
        write_manifest,
    )

    project = args.project.expanduser().resolve()
    sources = {
        name: getattr(args, name).expanduser().resolve()
        for name in ("shotlist", "alignment", "assets", "voice")
        if getattr(args, name) is not None
    }
    problems = [f"no {project / 'index.html'}"] * (not (project / "index.html").is_file())
    problems += [f"no {path}" for path in sources.values() if not path.exists()]
    if (project / MANIFEST_FILE).exists() and not args.force:
        problems.append(f"{project} already has {MANIFEST_FILE} (add --force to replace it)")
    if problems:
        print(f"error: {'; '.join(problems)}", file=sys.stderr)
        return 1
    shotlist = sources.get("shotlist")
    written = write_manifest(
        project,
        ProjectManifest(
            profile=args.profile,
            topic_id=args.topic,
            shotlist_sha256=file_sha256(shotlist) if shotlist else None,
            **sources,
        ),
    )
    print(f"wrote {written} (profile {args.profile})")
    return 0


def _app_running() -> bool:
    """Whether the HyperFrames desktop app is running (a process of its bundle)."""
    try:
        found = subprocess.run(["pgrep", "-f", APP_PROCESS], capture_output=True, check=False)
    except OSError:  # no pgrep: not a Mac, so no app
        return False
    return found.returncode == 0


def _new_chat(args: argparse.Namespace) -> int:
    """Move the project's run file aside, so the app opens the project with an empty chat.

    The app resumes one session per project from that file, and holds it in memory while it runs, so it must be quit
    first. The old file is kept under a name with the time.
    """
    runs = args.project.expanduser().resolve() / RUNS_FILE
    if not runs.is_file():
        print(f"nothing to reset: {runs} does not exist (the project has no chat yet)")
        return 0
    if _app_running():
        print(
            "error: the HyperFrames app is running; quit it completely (Cmd+Q), run this again, then reopen the project",
            file=sys.stderr,
        )
        return 1
    kept = runs.with_name(f"agent-runs.{datetime.now(UTC):%Y%m%d-%H%M%S}.jsonl")
    runs.rename(kept)
    print(f"moved the chat aside to {kept}: reopen the project and its chat starts empty")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run one command; returns the exit code."""
    args = build_parser().parse_args(argv)
    if args.command == "register":
        return _register(args)
    if args.command == "setup":
        return _setup()
    if args.command == "spend":
        return _spend(args)
    if args.command == "doctor":
        return _doctor()
    if args.command == "adopt":
        return _adopt(args)
    if args.command == "new-chat":
        return _new_chat(args)
    env.prepare()
    from hyperframes_mcp.server.server import build_server

    build_server().run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
