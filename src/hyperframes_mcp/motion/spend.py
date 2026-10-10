"""What an edit may spend: the limits, the quote that comes before every paid call, the confirm that makes the call, and
where the money is recorded.

The MCP server runs where nobody is asked before a tool runs, so the limits are held here, in code. A paid call is
first *quoted*: the quote holds the arguments, the price and, for a picture, the full prompt that would be sent, and is
kept in ``<project>/.video-mcp/quotes.json`` for ``quote_ttl_seconds``. The person is told what will be made and what it
costs; only then is the quote *confirmed*. Confirming prices the call again and refuses if anything moved (price,
prompt, model, profile, the still's bytes), if it would pass a limit, or if the quote expired; it makes the call once
(a second confirm of the same quote gives the first result back), and records the spend. What became of a quote's
calls is read from the spend logs, never from the quote file, which the agent can write:

* in ``<project>/.video-mcp/spend.jsonl``, append-only, which is where the project's limits are counted from, and in
  the central log ``~/.hyperframes-mcp/data/spend.jsonl``, which counts every project together;
* with a ``SpendSink`` when the host program installed one (``use_sink``): a program that embeds this package can
  refuse a call before it is paid (a budget) and take the record (a ledger). Without one, the two logs are all.

The limits are in ``limits.yaml`` (``per_call_usd``, ``per_project_usd``, optionally ``per_day_usd``); above them
the call is refused until the file is raised. Everything that makes a call is ``async``; quoting a picture and reading
the state are not. The providers are in ``generate.py`` and are imported only when a quote or a call needs them.

Beyond the dollar limits (``docs/spend-safety.md`` has the reasoning):

* **Hold before the call.** The price is written to the logs as a ``reserved`` line before anything is sent, and the
  line is replaced by what became of the call (the same quote and attempt number): the real spend (``confirmed``),
  nothing (``released``: it is known that nothing was billed), or ``uncertain`` (it failed after it was sent: it may
  have been billed). A call whose process died leaves its ``reserved`` line, which is a hold too. A hold counts against
  every limit until the person clears it (``hyperframes-mcp spend clear``), and a quote with a hold or a spend in the
  logs can never be sent again, so a retry cannot be billed twice. A quote whose attempts were all released may be sent
  again up to ``max_tries_per_quote`` times in all.
* **Calls, not only dollars.** ``max_calls_per_hour`` and ``max_calls_per_day`` count the attempts of all projects,
  the ones the provider refused too. Dollar limits trust the price table; call limits trust nothing, so a wrong price
  cannot run away.
* **Variables only tighten.** ``HYPERFRAMES_MCP_PER_CALL_USD`` and its siblings lower a limit of the file or the
  defaults and never raise one.
* **One switch.** ``HYPERFRAMES_MCP_SPEND`` set to anything but ``on``, or the ``FROZEN`` file, refuses every confirm;
  quoting still works.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hyperframes_mcp.motion.manifest import (
    MANIFEST_FILE,
    ManifestError,
    ProjectManifest,
    read_manifest,
)
from hyperframes_mcp.motion.profiles import DEFAULT_PROFILE
from hyperframes_mcp.motion.project_edit import AI_CREDIT, PICTURE_QUALITIES
from hyperframes_mcp.providers.video.budget import ClipBudget
from hyperframes_mcp.providers.video.errors import (
    ClipCapError,
    ClipLengthError,
    I2VError,
    MissingKeyError,
    StillError,
    UnknownModelError,
)
from hyperframes_mcp.providers.video.settings import I2VSettings, load_i2v_settings
from hyperframes_mcp.util.atomic import atomic_write_bytes
from hyperframes_mcp.util.paths import config_path, user_data_dir

CONFIG_PATH = config_path("limits.yaml")
STATE_DIR = ".video-mcp"
QUOTES_FILE = "quotes.json"
SPEND_LOG = "spend.jsonl"
# every confirmed spend of every project, outside all of them (the user's data folder): a project folder is the
# agent's to write, so its own log alone could be removed or a new folder started to count from nothing; tests point
# CENTRAL_LOG elsewhere
CENTRAL_LOG_NAME = "spend.jsonl"
CENTRAL_LOG: Path | None = None
LOCK_WAIT_SECONDS = 900.0  # how long a confirm waits for a paid call already running on any project
CLEAR_WAIT_SECONDS = 10.0  # how long the person's answer for a hold waits for one
_EPSILON = 1e-9
# provider and kind of what each paid call buys, as the spend log and a SpendSink name them
_LEDGER = {"picture": ("openai", "ai_image"), "clip": ("fal", "ai_video")}


class SpendSink(Protocol):
    """What a host program gives this package to hold the money to its own books (a budget, a cost ledger)."""

    async def check(self, project: Path, manifest: ProjectManifest, usd: float) -> None:
        """Called before a paid call, with its price; raise ``SpendError`` to refuse it (nothing is paid then)."""

    async def record(
        self,
        project: Path,
        manifest: ProjectManifest,
        usd: float,
        note: str,
        ref: str | None,
        *,
        provider: str,
        visual_type: str,
        quote_id: str | None,
    ) -> dict:
        """Called after the call with what it cost; returns a short description of where the spend went."""


_SINK: SpendSink | None = None


def use_sink(sink: SpendSink | None) -> None:
    """Install (or, with None, remove) the host program's ``SpendSink``."""
    global _SINK
    _SINK = sink


_CHANGED = {
    "still_sha256": "the still",
    "model": "the model",
    "profile": "the profile",
    "request": "the request",
}


class SpendError(RuntimeError):
    """A quote or confirm was refused; the message says why and what to do. Nothing was paid, unless the error says a
    paid call was made."""


class SpendLimits(BaseModel):
    """What one video may spend and how long a quote lasts (``spend`` in ``config/limits.yaml``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    per_call_usd: float = Field(default=1.0, gt=0)
    per_project_usd: float = Field(default=10.0, gt=0)
    per_day_usd: float | None = Field(default=None, gt=0)  # the project's spend in a UTC day; None: no limit
    quote_ttl_seconds: int = Field(default=900, gt=0)
    # every project together in a UTC day, counted from the central log; None: no limit
    all_projects_per_day_usd: float | None = Field(default=10.0, gt=0)
    # paid calls over every project, counted from the central log; they bound the harm of a wrong price; None: no limit
    max_calls_per_hour: int | None = Field(default=30, gt=0)
    max_calls_per_day: int | None = Field(default=100, gt=0)
    # how often one quote may be confirmed when the call was refused before it was sent
    max_tries_per_quote: int = Field(default=2, gt=0)


# the variables that tighten a limit: the environment of the server process (the app's chat does not see a shell's, so
# ``hyperframes-mcp register --env NAME=VALUE`` puts them in the registered entry)
VARIABLES: dict[str, tuple[str, type]] = {
    "per_call_usd": ("HYPERFRAMES_MCP_PER_CALL_USD", float),
    "per_project_usd": ("HYPERFRAMES_MCP_PER_PROJECT_USD", float),
    "per_day_usd": ("HYPERFRAMES_MCP_PER_DAY_USD", float),
    "all_projects_per_day_usd": ("HYPERFRAMES_MCP_ALL_PROJECTS_PER_DAY_USD", float),
    "max_calls_per_hour": ("HYPERFRAMES_MCP_MAX_CALLS_PER_HOUR", int),
    "max_calls_per_day": ("HYPERFRAMES_MCP_MAX_CALLS_PER_DAY", int),
}
FREEZE_VARIABLE = "HYPERFRAMES_MCP_SPEND"
FREEZE_FILE = "FROZEN"
_ALLOWED = "on"  # the one value of the variable that is not a freeze, besides leaving it unset


def variables_in_force() -> list[str]:
    """The names of the limit variables that are set in the environment."""
    return [name for name, _ in VARIABLES.values() if os.environ.get(name, "").strip()]


def _tightened(limits: SpendLimits) -> SpendLimits:
    """``limits`` with each variable applied: a variable lowers its limit, sets one the file left open, and never raises
    one.

    Raises:
        ValueError: a variable is not a positive number (the message names it).
    """
    changes: dict[str, Any] = {}
    for field, (name, kind) in VARIABLES.items():
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            value = kind(raw)
        except ValueError:
            value = 0
        if value <= 0:
            raise ValueError(f"{name} must be a positive number (it is {raw!r})")
        current = getattr(limits, field)
        changes[field] = value if current is None else min(current, value)
    return limits.model_copy(update=changes) if changes else limits


def load_spend_limits(profile_name: str = DEFAULT_PROFILE, path: Path | None = None) -> SpendLimits:
    """The limits of ``config/limits.yaml``, tightened by the variables; a missing file gives the defaults. A profile
    may override any of them under ``spend.profiles.<slug>``.

    Raises:
        ValueError: a value is malformed, a key is unknown or a variable is not a positive number.
    """
    try:
        data = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    section = dict(data.get("spend") or {})
    override = (section.pop("profiles", None) or {}).get(profile_name) or {}
    return _tightened(SpendLimits(**{**section, **override}))


def frozen() -> str | None:
    """Why paid calls are frozen (and how to allow them again), or None when they are not."""
    value = os.environ.get(FREEZE_VARIABLE, "").strip()
    if value and value.lower() != _ALLOWED:
        return f"{FREEZE_VARIABLE} is {value!r}; set it to 'on' or unset it to allow them again"
    if os.path.lexists(user_data_dir() / FREEZE_FILE):  # a file, a folder or a dangling link: whatever is there freezes
        return "the person ran `hyperframes-mcp spend freeze`; `hyperframes-mcp spend thaw` allows them again"
    return None


def freeze() -> Path:
    """Refuse every paid call until ``thaw``; returns the marker file."""
    path = user_data_dir() / FREEZE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat(timespec="seconds") + "\n", encoding="utf-8")
    return path


def thaw() -> bool:
    """Allow paid calls again; False when the marker was not there."""
    path = user_data_dir() / FREEZE_FILE
    existed = os.path.lexists(path)
    if path.is_dir() and not path.is_symlink():
        path.rmdir()
    else:
        path.unlink(missing_ok=True)
    return existed


class Quote(BaseModel):
    """What a paid call would make and cost, kept until it is confirmed or expires.

    ``arguments`` are the caller's inputs plus what the call also depends on (model, profile, the still's hash);
    ``clip_cap_left`` is what the project's clip log still allows. Whether and how often the quote was confirmed is not
    kept here: the file is the agent's to write, so the spend logs say.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["picture", "clip"]
    arguments: dict[str, Any]
    arguments_sha256: str
    price_usd: float
    created_at: datetime
    expires_at: datetime
    prompt: str | None = None
    clip_cap_left: dict[str, float] | None = None


class _QuoteBook(BaseModel):
    quotes: dict[str, Quote] = Field(default_factory=dict)


@dataclass(frozen=True)
class _Prepared:
    """A call as it would be made now: the arguments, the price, the picture's prompt and the clip cap left."""

    arguments: dict[str, Any]
    price: float
    prompt: str | None = None
    cap_left: dict[str, float] | None = None


# -- the project --------------------------------------------------------------------------------------------------


def _generate():
    from hyperframes_mcp.motion import generate

    return generate


def _manifest(project: Path) -> ProjectManifest:
    """The project's manifest; a folder without one is a loose project (default profile, no topic)."""
    if not (project / MANIFEST_FILE).is_file():
        return ProjectManifest()
    try:
        return read_manifest(project)
    except ManifestError as error:
        raise SpendError(str(error)) from error


def _settings(project: Path) -> tuple[ProjectManifest, SpendLimits]:
    manifest = _manifest(project)
    try:
        return manifest, load_spend_limits(manifest.profile)
    except ValueError as error:
        raise SpendError(f"{CONFIG_PATH.name} cannot be read: {error}") from error


def _inside(project: Path, path: str | Path, what: str) -> Path:
    """``path`` (relative to the project, or absolute) as a resolved path that must lie inside the project."""
    target = Path(path)
    target = (target if target.is_absolute() else project / target).resolve()
    if not target.is_relative_to(project):
        raise SpendError(f"{what} {path} is outside the project {project}")
    return target


def _relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def _state_dir(project: Path) -> Path:
    folder = project / STATE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# -- what was spent -----------------------------------------------------------------------------------------------


def central_log() -> Path:
    """Where every project's confirmed spend is also logged: ``CENTRAL_LOG``, else the user's data folder."""
    return CENTRAL_LOG or user_data_dir() / CENTRAL_LOG_NAME


_STATUSES = ("confirmed", "reserved", "uncertain", "released")


def _when(line: dict) -> datetime:
    """When a log line was written, as an aware time (a line without a zone is read as UTC)."""
    moment = datetime.fromisoformat(line["at"])
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _problem_of(line: object) -> str | None:
    """Why a parsed log line cannot be counted, or None when it can. An amount that is not a plain non-negative number
    would make every comparison with a limit false, so it is a problem like unreadable text."""
    if not isinstance(line, dict):
        return "it is not an object"
    usd = line.get("usd")
    try:
        usable = not isinstance(usd, bool) and isinstance(usd, (int, float)) and math.isfinite(usd) and usd >= 0
    except OverflowError:  # an integer too large for a float
        usable = False
    if not usable:
        return "usd is not a number of dollars"
    try:
        _when(line).astimezone(UTC)  # a time that cannot be put in UTC (year 1 with an offset) cannot be counted
    except (KeyError, TypeError, ValueError, OverflowError):
        return "at is not a time"
    if line.get("quote") is not None and not isinstance(line["quote"], str):
        return "quote is not text"
    if line.get("status", "confirmed") not in _STATUSES:
        return f"status is {line.get('status')!r}"
    attempt = line.get("attempt", 1)
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        return "attempt is not a number"
    return None


def _read_log(path: Path) -> tuple[list[dict], list[str]]:
    """The lines of a spend log that can be counted, and a problem text for each line that cannot."""
    if not path.is_file():
        return [], []
    lines: list[dict] = []
    problems: list[str] = []
    for number, text in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not text.strip():
            continue
        try:
            line = json.loads(text)
        except ValueError as error:
            problems.append(f"{path} line {number} cannot be read ({error})")
            continue
        if (why := _problem_of(line)) is not None:
            problems.append(f"{path} line {number} cannot be counted ({why})")
            continue
        lines.append(line)
    return lines, problems


def _log_lines(path: Path) -> list[dict]:
    """Every line of a spend log. A line that cannot be counted stops all spending: counting less than was spent would
    let a limit be passed."""
    lines, problems = _read_log(path)
    if problems:
        raise SpendError(f"{problems[0]}; mend or remove it: nothing may be spent while the spend cannot be counted")
    return lines


def _spend_lines(project: Path) -> list[dict]:
    """The project's own spend log."""
    return _log_lines(project / STATE_DIR / SPEND_LOG)


_HOLDING = ("reserved", "uncertain")
_RANK = {"released": 0, "reserved": 1, "uncertain": 2, "confirmed": 3}


def _attempt_key(line: dict) -> tuple[str | None, str, int]:
    """Which attempt of which quote of which project a line is about (the project's own log has no ``project``: all
    its lines are one project's)."""
    return line.get("project"), line["quote"], int(line.get("attempt", 1))


def _attempts(lines: list[dict]) -> list[dict]:
    """The latest line of each attempt of each quote of each project (a hold is replaced by what became of the call),
    whatever became of it: every attempt that was made, the refused ones included. Two projects' lines of the same quote
    id stay apart."""
    latest: dict[tuple[str | None, str, int], dict] = {}
    for line in lines:
        if line.get("quote") is not None:
            latest[_attempt_key(line)] = line
    return list(latest.values())


def _counted(lines: list[dict]) -> list[dict]:
    """The lines that count as spend: every line that has no quote (a spend noted by hand), and of each attempt of a
    quote only the latest line, none of a released one."""
    plain = [line for line in lines if line.get("quote") is None]
    return [*plain, *(line for line in _attempts(lines) if line.get("status", "confirmed") != "released")]


def _holds_of(lines: list[dict]) -> list[dict]:
    """The holds among the counted lines: a call that is running, or that may have been billed."""
    return [line for line in _counted(lines) if line.get("status") in _HOLDING]


def _merged(project: Path, own_lines: list[dict], central_lines: list[dict]) -> list[dict]:
    """The latest line of each attempt of the project's quotes. The central log wins where it has the attempt: it is
    written first and lies outside the project, so a line of the project's own log that disagrees is a write that failed
    or was changed. An attempt only the project's own log has (the central log was lost) counts as it says."""
    folder_id = _folder_id(project)
    merged = {(line["quote"], int(line.get("attempt", 1))): line for line in _attempts(own_lines)}
    merged.update(
        {
            (line["quote"], int(line.get("attempt", 1))): line
            for line in _attempts([line for line in central_lines if _is_project(line, project, folder_id)])
        }
    )
    return list(merged.values())


def _history(project: Path, quote_id: str) -> dict[int, dict]:
    """What became of each attempt to make a quote's call, by attempt number, read from the project's log and the
    central one together (the central one wins, see ``_merged``)."""
    lines = _merged(project, _spend_lines(project), _log_lines(central_log()))
    return {int(line.get("attempt", 1)): line for line in lines if line["quote"] == quote_id}


@dataclass(frozen=True)
class _Totals:
    spent: float  # the project in all
    today: float  # the project in the UTC day
    calls: int
    all_today: float  # every project together in the UTC day
    calls_hour: int  # paid calls of every project in the last hour
    calls_today: int  # paid calls of every project in the UTC day
    holds: list[dict]  # the project's calls that are running or may have been billed


def _totals(project: Path, now: datetime) -> _Totals:
    """What the project has spent, in all and in the UTC day of ``now``, and every project together that day. A
    project's figures come from its own log and the central log's lines for it together (``_merged``), so removing its
    own log does not start its count again."""
    today = now.astimezone(UTC).date()
    hour_ago = now - timedelta(hours=1)

    def on_day(line: dict) -> bool:
        return _when(line).astimezone(UTC).date() == today

    own_lines = _spend_lines(project)
    central_lines = _log_lines(central_log())
    central = _counted(central_lines)
    # a spend noted by hand sits in both logs: count the larger set once; the attempts of quotes are merged by attempt
    own_plain = [line for line in own_lines if line.get("quote") is None]
    folder_id = _folder_id(project)
    mine_plain = [line for line in central_lines if line.get("quote") is None and _is_project(line, project, folder_id)]
    plain = own_plain if sum(line["usd"] for line in own_plain) >= sum(line["usd"] for line in mine_plain) else mine_plain
    attempts = _merged(project, own_lines, central_lines)
    mine = [*plain, *(line for line in attempts if line.get("status", "confirmed") != "released")]
    calls_of_server = _attempts(central_lines)  # every attempt counts as a call, the refused ones too
    return _Totals(
        spent=sum(line["usd"] for line in mine),
        today=sum(line["usd"] for line in mine if on_day(line)),
        calls=len(mine),
        all_today=sum(line["usd"] for line in central if on_day(line)),
        calls_hour=sum(1 for line in calls_of_server if _when(line) >= hour_ago),
        calls_today=sum(1 for line in calls_of_server if on_day(line)),
        holds=[line for line in mine if line.get("status") in _HOLDING],
    )


def spend_state(project: str | Path, *, now: datetime | None = None) -> dict:
    """What the project has spent, its limits and what is left of them (all USD)."""
    project = Path(project).resolve()
    manifest, limits = _settings(project)
    totals = _totals(project, now or datetime.now(UTC))
    spent, today, calls = totals.spent, totals.today, totals.calls
    left_project = limits.per_project_usd - spent
    left_day = None if limits.per_day_usd is None else limits.per_day_usd - today
    left_all = None if limits.all_projects_per_day_usd is None else limits.all_projects_per_day_usd - totals.all_today
    allowed = [limits.per_call_usd, left_project] + [left for left in (left_day, left_all) if left is not None]
    return {
        "project": str(project),
        "profile": manifest.profile,
        "topic_id": manifest.topic_id,
        "spent_usd": round(spent, 6),
        "spent_today_usd": round(today, 6),
        "calls": calls,
        "limits": {
            "per_call_usd": limits.per_call_usd,
            "per_project_usd": limits.per_project_usd,
            "per_day_usd": limits.per_day_usd,
            "all_projects_per_day_usd": limits.all_projects_per_day_usd,
            "max_calls_per_hour": limits.max_calls_per_hour,
            "max_calls_per_day": limits.max_calls_per_day,
        },
        "all_projects_spent_today_usd": round(totals.all_today, 6),
        "calls_last_hour": totals.calls_hour,
        "calls_today_all_projects": totals.calls_today,
        "uncertain": [
            {
                "quote_id": hold["quote"],
                "usd": hold["usd"],
                "since": hold["at"],
                "status": hold["status"],
                "note": hold.get("note"),
            }
            for hold in totals.holds
        ],
        "frozen": frozen(),
        "variables_in_force": variables_in_force(),
        "left_usd": {
            "project": round(left_project, 6),
            "day": None if left_day is None else round(left_day, 6),
            "all_projects_day": None if left_all is None else round(left_all, 6),
            "next_call": round(max(min(allowed), 0.0), 6),
        },
    }


def _check_limits(project: Path, limits: SpendLimits, price: float, now: datetime) -> None:
    if not math.isfinite(price) or price < 0:  # every comparison with a limit would be false
        raise SpendError(f"the price is {price!r}, not a number of dollars; the price table cannot be used")
    if price > limits.per_call_usd + _EPSILON:
        raise SpendError(
            f"this costs about ${price:.2f}, over the ${limits.per_call_usd:.2f} limit for one call "
            "(per_call_usd in config/limits.yaml)"
        )
    totals = _totals(project, now)
    spent, today = totals.spent, totals.today
    if spent + price > limits.per_project_usd + _EPSILON:
        raise SpendError(
            f"the project has spent ${spent:.2f} of ${limits.per_project_usd:.2f}; ${price:.2f} more would pass "
            "the limit (per_project_usd in config/limits.yaml)"
        )
    if limits.per_day_usd is not None and today + price > limits.per_day_usd + _EPSILON:
        raise SpendError(
            f"today the project has spent ${today:.2f} of ${limits.per_day_usd:.2f}; ${price:.2f} more would "
            "pass the limit (per_day_usd in config/limits.yaml)"
        )
    cap = limits.all_projects_per_day_usd
    if cap is not None and totals.all_today + price > cap + _EPSILON:
        raise SpendError(
            f"today every project together has spent ${totals.all_today:.2f} of ${cap:.2f}; ${price:.2f} more "
            "would pass the limit (all_projects_per_day_usd in config/limits.yaml)"
        )
    if limits.max_calls_per_hour is not None and totals.calls_hour >= limits.max_calls_per_hour:
        raise SpendError(
            f"{totals.calls_hour} paid calls in the last hour already (max_calls_per_hour is {limits.max_calls_per_hour} "
            "in config/limits.yaml, or HYPERFRAMES_MCP_MAX_CALLS_PER_HOUR); wait, or ask the person to raise it if "
            "these were meant"
        )
    if limits.max_calls_per_day is not None and totals.calls_today >= limits.max_calls_per_day:
        raise SpendError(
            f"{totals.calls_today} paid calls today already across every project (max_calls_per_day is "
            f"{limits.max_calls_per_day} in config/limits.yaml, or HYPERFRAMES_MCP_MAX_CALLS_PER_DAY); ask the person "
            "to raise it if these were meant"
        )


def _flush_to_disk(fd: int) -> None:
    """Make what was written to ``fd`` reach the disk (macOS needs ``F_FULLFSYNC`` for that; ``fsync`` stops at the
    drive's cache)."""
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if full is not None:
        try:
            fcntl.fcntl(fd, full)
            return
        except OSError:
            pass
    os.fsync(fd)


def _folder_id(project: Path) -> str | None:
    """Which folder on the disk a project is, by device and inode: the spelling of its path (case on a case-insensitive
    disk, a link) and its name can change, the folder does not."""
    try:
        status = project.stat()
    except OSError:
        return None
    return f"{status.st_dev}:{status.st_ino}"


def _is_project(line: dict, project: Path, folder_id: str | None) -> bool:
    """Whether a central log line is about this project: the same path, or the same folder under another name."""
    return line.get("project") == str(project) or (folder_id is not None and line.get("project_id") == folder_id)


def _append_line(path: Path, text: str) -> None:
    """Append ``text`` as one line of ``path`` and make sure it reached the disk. A last line that was cut short (no
    newline) gets one first, so the new line does not grow onto the old."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    with path.open("a+b") as log:
        size = log.seek(0, os.SEEK_END)
        if size:
            log.seek(size - 1)
            if log.read(1) != b"\n":
                log.write(b"\n")
        log.write(text.encode("utf-8") + b"\n")
        log.flush()
        _flush_to_disk(log.fileno())
    if created:  # the new file's name must reach the disk too
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def _append_spend(
    project: Path, entry: dict, *, own: bool = True, strict: bool = True, project_id: str | None = None
) -> str | None:
    """Log a spend in the central log (with the project's folder and its identity) and in the project's own log. The
    central one goes first: a process that dies between the two writes leaves the line where the agent cannot remove
    it. ``own=False`` leaves out the project's log, for a project whose folder is gone.

    Either log may be the one that breaks (a full disk, a folder put where the file is). With ``strict`` a failure
    raises, which is right before a call is sent (the caller undoes what was written). After the call, ``strict=False``
    writes what it can and goes on, because the money is spent: the failures come back as a message.
    """
    problems = []
    central = {**entry, "project": str(project), "project_id": project_id or _folder_id(project)}
    try:
        _append_line(central_log(), json.dumps(central))
    except OSError as error:
        if strict:
            raise SpendError(f"the central spend log cannot be written ({error}); nothing was sent") from error
        problems.append(f"the central spend log could not be written ({error})")
    if own:
        try:
            _append_line(_state_dir(project) / SPEND_LOG, json.dumps(entry))
        except OSError as error:
            if strict:
                raise SpendError(f"the project's spend log cannot be written ({error}); nothing was sent") from error
            problems.append(f"the project's own spend log could not be written ({error})")
    return "; ".join(problems) or None


# -- the quotes ---------------------------------------------------------------------------------------------------


def _sha(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True).encode("utf-8")).hexdigest()


def _read_book(folder: Path) -> _QuoteBook:
    path = folder / QUOTES_FILE
    if not path.is_file():
        return _QuoteBook()
    try:
        book = _QuoteBook.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        raise SpendError(
            f"{path} cannot be read ({error.error_count()} problem(s)); remove it and quote again"
        ) from error
    if any(key != quote.id for key, quote in book.quotes.items()):
        # the logs are keyed by the quote's id: an entry filed under another name would be a way to send a call as
        # an attempt of a quote that is already spent
        raise SpendError(f"{path} cannot be read (an entry is filed under another quote's id); remove it and quote again")
    return book


@contextmanager
def _quote_book(project: Path) -> Iterator[_QuoteBook]:
    """The project's quotes under a short lock, written back when the block ends without an error."""
    folder = _state_dir(project)
    with (folder / "quotes.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        book = _read_book(folder)
        yield book
        atomic_write_bytes(folder / QUOTES_FILE, (book.model_dump_json(indent=2) + "\n").encode())


def _issue(project: Path, kind: str, prepared: _Prepared, save: bool) -> Quote:
    now = datetime.now(UTC)
    limits = _settings(project)[1]
    _check_limits(project, limits, prepared.price, now)
    quote = Quote(
        id=f"q-{uuid.uuid4().hex[:8]}",
        kind=kind,
        arguments=prepared.arguments,
        arguments_sha256=_sha(prepared.arguments),
        price_usd=prepared.price,
        created_at=now,
        expires_at=now + timedelta(seconds=limits.quote_ttl_seconds),
        prompt=prepared.prompt,
        clip_cap_left=prepared.cap_left,
    )
    if save:
        with _quote_book(project) as book:
            book.quotes = {
                key: kept for key, kept in book.quotes.items() if kept.expires_at > now
            }
            book.quotes[quote.id] = quote
    return quote


def _refuse_existing(target: Path, project: Path) -> None:
    """A paid file is never written over another: the one already there may be a picture that was paid for."""
    if os.path.lexists(target):
        raise SpendError(
            f"{_relative(project, target)} already exists; choose another name (a file that was made or paid for is "
            "never written over)"
        )


def _picture(
    project: Path,
    manifest: ProjectManifest,
    request: str,
    quality: str,
    out: str | Path,
) -> _Prepared:
    generate = _generate()
    if quality not in PICTURE_QUALITIES:
        raise SpendError(f"quality is one of {', '.join(PICTURE_QUALITIES)}")
    if not request.strip():
        raise SpendError("say what the picture shows")
    target = _inside(project, out, "the picture's file")
    _refuse_existing(target, project)
    try:
        generate.picture_format(target)
    except generate.GenerateError as error:
        raise SpendError(str(error)) from error
    try:
        prompt = generate.picture_prompt(request, manifest.profile)
    except ValueError as error:
        raise SpendError(f"profile {manifest.profile!r}: {error}") from error
    return _Prepared(
        {
            "request": request,
            "quality": quality,
            "out": _relative(project, target),
            "model": generate.PICTURE_MODEL,
            "profile": manifest.profile,
        },
        generate.picture_price(quality),
        prompt,
    )


async def _clip_cap_left(project: Path, settings: I2VSettings) -> dict[str, float]:
    """What the project's own clip log still allows: clips, seconds and USD of attempts."""
    from hyperframes_mcp.motion.generate import CLIP_CAP_ID, CLIP_LOG

    log, limits = project / CLIP_LOG, settings.limits
    used = await ClipBudget(limits, log).usage(CLIP_CAP_ID) if log.is_file() else None
    return {
        "clips": limits.max_clips - (used.clips if used else 0),
        "seconds": limits.max_total_seconds - (used.seconds if used else 0),
        "usd": round(limits.max_spend_usd - (used.spend_usd if used else 0), 6),
    }


async def _clip(
    project: Path,
    manifest: ProjectManifest,
    still: str | Path,
    motion: str,
    seconds: int,
    out: str | Path,
) -> _Prepared:
    generate = _generate()
    source = _inside(project, still, "the still")
    if not source.is_file():
        raise SpendError(f"the still {still} does not exist")
    if not motion.strip():
        raise SpendError("say what moves and how the camera moves")
    target = _inside(project, out, "the clip's file")
    _refuse_existing(target, project)
    settings = load_i2v_settings()
    try:
        price = generate.clip_price(seconds)
    except I2VError as error:
        raise SpendError(str(error)) from error
    return _Prepared(
        {
            "still": _relative(project, source),
            "still_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "motion": motion,
            "seconds": seconds,
            "out": _relative(project, target),
            "model": settings.model,
            "profile": manifest.profile,
        },
        price,
        cap_left=await _clip_cap_left(project, settings),
    )


def quote_picture(
    project: str | Path,
    request: str,
    *,
    quality: str = "high",
    out: str | Path,
    save: bool = True,
) -> Quote:
    """Quote one picture of ``request`` into ``out`` (inside the project): its price and the full prompt that would be
    sent, in the style of the project's profile. Nothing is called. ``save=False`` gives the quote without keeping it
    (a dry run).

    Raises:
        SpendError: the picture cannot be quoted: a bad request or file, or a limit would be passed.
    """
    project = Path(project).resolve()
    manifest = _manifest(project)
    return _issue(project, "picture", _picture(project, manifest, request, quality, out), save)


async def quote_clip(
    project: str | Path,
    still: str | Path,
    motion: str,
    *,
    seconds: int,
    out: str | Path,
    save: bool = True,
) -> Quote:
    """Quote one ``seconds`` clip made from ``still`` (``motion`` says what moves) into ``out``, both inside the
    project: its price, and the project's clip cap left. Nothing is called.

    Raises:
        SpendError: the clip cannot be quoted: the model cannot make that length (the message names the lengths it
            can), a file is missing or outside the project, or a limit would be passed.
    """
    project = Path(project).resolve()
    manifest = _manifest(project)
    prepared = await _clip(project, manifest, still, motion, seconds, out)
    return _issue(project, "clip", prepared, save)


# -- the confirm --------------------------------------------------------------------------------------------------


@contextmanager
def _paying_sync(wait: float | None = None) -> Iterator[None]:
    """The paid-call lock for code that is not async (the person answering for a hold): a clear must not land between a
    running call's reservation and its result."""
    path = central_log().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        deadline = time.monotonic() + (CLEAR_WAIT_SECONDS if wait is None else wait)
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SpendError(
                        "a paid call is running; answer for the hold when it is done (a running call is not a hold)"
                    ) from None
                time.sleep(0.1)
        yield


@asynccontextmanager
async def _paying():
    """One paid call at a time across every project and process, so two videos cannot both pass the all-projects
    daily limit between its check and the record. It polls, so that a waiting call in this process does not hold up
    the one that has the lock."""
    path = central_log().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SpendError(
                        "another paid call (on this or another video) is still running; confirm again when it is done"
                    ) from None
                await asyncio.sleep(0.1)
        yield


async def _prepare(project: Path, manifest: ProjectManifest, kind: str, given: dict[str, Any]) -> _Prepared:
    """A quote's call, worked out again from the inputs it was made with."""
    if kind == "picture":
        return _picture(project, manifest, given["request"], given["quality"], given["out"])
    return await _clip(
        project,
        manifest,
        given["still"],
        given["motion"],
        given["seconds"],
        given["out"],
    )


def _refuse_drift(quote: Quote, fresh: _Prepared) -> None:
    if abs(fresh.price - quote.price_usd) > _EPSILON:
        raise SpendError(
            f"the price changed from ${quote.price_usd:.3f} to ${fresh.price:.3f} since quote {quote.id}; quote again"
        )
    if fresh.prompt != quote.prompt:
        raise SpendError(
            f"the picture prompt changed since quote {quote.id} (the profile's style was edited); quote again"
        )
    changed = [_CHANGED.get(key, key) for key, value in fresh.arguments.items() if value != quote.arguments.get(key)]
    if changed:
        raise SpendError(f"{', '.join(changed)} changed since quote {quote.id}; quote again")


async def _make(project: Path, manifest: ProjectManifest, quote: Quote) -> dict:
    """The paid call of a checked quote."""
    generate = _generate()
    args = quote.arguments
    out = project / args["out"]
    if quote.kind == "picture":
        return await generate.make_picture(
            args["request"],
            out,
            quality=args["quality"],
            profile_name=manifest.profile,
            canvas=manifest.canvas,
        )
    return await generate.make_clip(
        project / args["still"],
        args["motion"],
        out,
        project=project,
        seconds=args["seconds"],
        topic_id=manifest.topic_id,
        canvas=manifest.canvas,
    )


def _refused_before_sending(error: BaseException) -> bool:
    """Whether a failed call is known not to have been billed.

    The code that made the call knows best: it marks an error with ``may_be_billed`` (the video adapter does, from
    whether fal accepted the job; the picture and clip makers do for what fails after the provider answered). Without
    that mark: the package's own checks (no key, a bad length, a cap) and an HTTP answer in the 4xx range (the provider
    read the request and said no) were refused before billing. Anything else — a timeout, a dropped connection, a 5xx,
    a policy failure nobody said was early, a process that was stopped — leaves the call possibly billed."""
    marked = getattr(error, "may_be_billed", None)
    if isinstance(marked, bool):
        return not marked
    generate = _generate()
    if isinstance(
        error, (generate.GenerateError, ClipCapError, ClipLengthError, UnknownModelError, StillError, MissingKeyError)
    ):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500 and status != 408


def _words(error: BaseException) -> str:
    """What a failure says: the package's own messages in full, anything from a provider by its kind and HTTP status
    only (its text can carry request details)."""
    if isinstance(error, (_generate().GenerateError, I2VError)):
        return str(error)
    status = getattr(error, "status_code", None)
    return type(error).__name__ + (f", HTTP {status}" if isinstance(status, int) else "")


def _usable_cost(made: dict, quoted: float) -> tuple[float, bool]:
    """The cost the provider's answer reports, and whether it is a usable one. A cost that is missing, not finite or
    negative is replaced by the quoted price: a limit cannot be counted with it."""
    try:
        cost = float(made["cost_usd"])
    except (KeyError, TypeError, ValueError):
        return quoted, False
    return (cost, True) if math.isfinite(cost) and cost >= 0 else (quoted, False)


def _warning(made: dict, cost: float, quoted: float, usable: bool = True) -> str:
    notes = [made["warning"]] if "warning" in made else []
    if not usable:
        notes.append(f"the provider's answer has not a usable cost; ${quoted:.3f}, the quoted price, is recorded")
    if cost > quoted + 0.005:
        notes.append(f"the call cost ${cost:.3f}, above its quote of ${quoted:.3f}; the price table may be out of date")
    return "; ".join(notes)


def _hold(project: Path, quote: Quote, usd: float, status: str, provider: str, note: str, *, attempt: int = 1) -> None:
    """Write a line about one attempt to make the quote's call; it replaces the earlier lines of that attempt in the
    counts. The line that reserves the price is strict about both logs (nothing is sent without it); the lines after the
    call write what they can and go on, because the money is spent."""
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "usd": round(usd, 6),
        "provider": provider,
        "note": note,
        "ref": None,
        "quote": quote.id,
        "attempt": attempt,
        "status": status,
    }
    try:
        _append_spend(project, entry, strict=status == "reserved")
    except SpendError:
        # the reservation reached the central log but the project's could not be written: nothing is sent, so it must
        # not stay a hold (a hold that nothing can clear would use up the day's budget)
        try:
            _append_line(
                central_log(),
                json.dumps({**entry, "usd": 0.0, "status": "released", "note": "not sent: the reservation could not be completed",
                            "project": str(project), "project_id": _folder_id(project)}),
            )  # fmt: skip
        except OSError:
            pass
        raise


async def confirm(
    project: str | Path,
    quote_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Make the call a quote describes, once, and record the spend; returns what was made.

    The quote is priced again first: a price, prompt, model, profile or still that moved since the quote, a quote that
    expired, a call that would pass a limit, or one the host's ``SpendSink`` refuses, is refused before anything is
    paid. A second confirm of a quote already made returns the first result (``replayed: true``) and pays nothing. One
    call runs at a time across every project and process. The keys are the caller's business (``keys.load_keys``).

    Returns:
        ``quote_id``, ``kind``, ``path`` (inside the project), ``cost_usd``, ``credit``, ``recorded`` (where the
        spend went, see ``record_spend``), ``spent_usd`` and ``left_usd`` of the project, and a ``warning`` when the
        file was kept but could not be fitted to the canvas.

    Raises:
        SpendError: the call was refused or failed; nothing was paid unless the message says it was.
    """
    project = Path(project).resolve()
    async with _paying():
        now = now or datetime.now(UTC)  # read after the wait for the lock, which can take minutes and cross midnight
        quote = _read_book(_state_dir(project)).quotes.get(quote_id)
        history = _history(project, quote_id)
        if quote is None:
            if history:
                raise SpendError(
                    f"quote {quote_id} was already used (see `hyperframes-mcp spend status`); quote the picture or clip again"
                )
            raise SpendError(
                f"no quote {quote_id} for this project (a quote lasts only until it expires); "
                "quote the picture or clip again"
            )
        if any(line.get("status", "confirmed") == "confirmed" for line in history.values()):
            return _replay(project, quote, history)
        if _sha(quote.arguments) != quote.arguments_sha256:
            raise SpendError(f"quote {quote.id} was edited: its arguments do not match their hash; quote again")
        if now >= quote.expires_at:
            raise SpendError(f"quote {quote.id} expired at {quote.expires_at:%H:%M:%S} UTC; quote again")
        if stuck := [line for line in history.values() if line.get("status") in _HOLDING]:
            raise SpendError(
                f"quote {quote.id} may have been billed already: its call "
                + ("was interrupted after it was sent" if stuck[0]["status"] == "reserved" else "failed after it was sent")
                + f", and its price stays counted. The person checks the provider's dashboard and runs "
                f"`hyperframes-mcp spend clear {quote.id} --billed` or `--not-billed`; then quote again"
            )
        manifest, limits = _settings(project)
        if reason := frozen():
            raise SpendError(f"paid calls are frozen ({reason}); a price can still be quoted")
        if len(history) >= limits.max_tries_per_quote:
            raise SpendError(
                f"quote {quote.id} has used its {limits.max_tries_per_quote} tries (max_tries_per_quote); quote again"
            )
        fresh = await _prepare(project, manifest, quote.kind, quote.arguments)
        _refuse_drift(quote, fresh)
        _check_limits(project, limits, fresh.price, now)
        if _SINK is not None:
            try:
                await _SINK.check(project, manifest, fresh.price)
            except SpendError:
                raise
            except Exception as error:  # noqa: BLE001
                raise SpendError(
                    f"the host program's budget could not be checked ({type(error).__name__}: {error}); nothing was paid"
                ) from error
        if reason := frozen():  # the person may have frozen paid calls while the checks above waited
            raise SpendError(f"paid calls are frozen ({reason}); a price can still be quoted")

        provider, visual_type = _LEDGER[quote.kind]
        attempt = max(history, default=0) + 1
        _hold(
            project, quote, fresh.price, "reserved", provider, f"held for {quote.kind} {quote.arguments['out']}",
            attempt=attempt,
        )  # fmt: skip
        try:
            made = await _make(project, manifest, quote)
        except BaseException as error:
            if _refused_before_sending(error):
                _hold(
                    project, quote, 0.0, "released", provider, f"refused before it was sent: {type(error).__name__}",
                    attempt=attempt,
                )  # fmt: skip
                raise SpendError(_words(error)) from error
            _hold(
                project, quote, fresh.price, "uncertain", provider, f"may have been billed: {type(error).__name__}",
                attempt=attempt,
            )  # fmt: skip
            if not isinstance(error, Exception):  # a cancellation or an interrupt goes on, its hold kept
                raise
            raise SpendError(
                f"the call failed after it was sent ({_words(error)}); it may have been billed, so ${fresh.price:.2f} "
                f"stays counted against the limits. The person checks the provider's dashboard and runs "
                f"`hyperframes-mcp spend clear {quote.id} --billed` or `--not-billed`; then quote again"
            ) from error
        out = (project / quote.arguments["out"]).resolve()
        cost, usable = _usable_cost(made, fresh.price)
        recorded = await record_spend(
            project,
            cost,
            f"{quote.kind} {quote.arguments['out']} for {project.name} (video MCP quote {quote.id})",
            str(out),
            provider=provider,
            visual_type=visual_type,
            quote_id=quote.id,
            attempt=attempt,
        )
        state = spend_state(project, now=now)
        return {
            "quote_id": quote.id,
            "kind": quote.kind,
            "path": quote.arguments["out"],
            "cost_usd": cost,
            "credit": made["credit"],
            "recorded": recorded,
            "spent_usd": state["spent_usd"],
            "left_usd": state["left_usd"]["project"],
            **(
                {"warning": _warning(made, cost, fresh.price, usable)}
                if "warning" in made or cost > fresh.price + 0.005 or not usable
                else {}
            ),
        }


def _replay(project: Path, quote: Quote, history: dict[int, dict]) -> dict:
    """The answer for a quote the logs say was made already, worked out from the logs and the quote's own file name,
    never from anything the quote file claims about the result."""
    made = max(
        (line for line in history.values() if line.get("status", "confirmed") == "confirmed"),
        key=lambda line: int(line.get("attempt", 1)),
    )
    if not made.get("ref"):  # the person answered for it: no file was recorded as its result
        raise SpendError(
            f"quote {quote.id} was already billed (${made['usd']:.2f}) but no file was recorded for it; "
            "quote again for a new file"
        )
    target = _inside(project, quote.arguments["out"], "the file")
    logged = Path(made["ref"])
    if not (target.is_file() and logged.exists() and os.path.samefile(logged, target)):
        raise SpendError(
            f"quote {quote.id} was already made and billed (${made['usd']:.2f}) as {made['ref']}, but "
            f"{quote.arguments['out']} is not that file; quote again for a new file"
        )
    state = spend_state(project)
    return {
        "quote_id": quote.id,
        "kind": quote.kind,
        "path": _relative(project, target),
        "cost_usd": made["usd"],
        "credit": AI_CREDIT,
        "recorded": {"where": "spend_log"},
        "spent_usd": state["spent_usd"],
        "left_usd": state["left_usd"]["project"],
        "replayed": True,
    }


# -- the record ---------------------------------------------------------------------------------------------------


async def record_spend(
    project: str | Path,
    usd: float,
    note: str,
    ref: str | None,
    *,
    provider: str,
    visual_type: str,
    quote_id: str | None = None,
    attempt: int = 1,
) -> dict:
    """Record ``usd`` the project spent: in its spend log first (the limits count from there), then with the host's
    ``SpendSink`` when there is one. ``ref`` is the file it made, if any; ``provider`` and ``visual_type`` name what was
    bought (``openai`` and ``ai_image``, ``fal`` and ``ai_video``).

    The money is already spent, so a sink that fails does not raise: the spend still counts for the project's limits and
    the returned dict says so.

    Returns:
        ``{"where": "spend_log"}``, with ``"sink"`` (what the sink said) or ``"sink_error"`` when there is one.
    """
    project = Path(project).resolve()
    manifest = _manifest(project)
    own_log_problem = _append_spend(
        project,
        {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "usd": round(usd, 6),
            "provider": provider,
            "note": note,
            "ref": ref,
            "quote": quote_id,
            "attempt": attempt,
            "status": "confirmed",
        },
        strict=False,
    )
    where = {"where": "spend_log", **({"own_log_problem": own_log_problem} if own_log_problem else {})}
    if _SINK is None:
        return where
    try:
        told = await _SINK.record(
            project,
            manifest,
            usd,
            note,
            ref,
            provider=provider,
            visual_type=visual_type,
            quote_id=quote_id,
        )
    except Exception as error:  # noqa: BLE001
        # the call is paid for: failing to record it must not turn it into a failure
        return {**where, "sink_error": f"{type(error).__name__}: {error}"}
    return {**where, "sink": told}


# -- the holds ----------------------------------------------------------------------------------------------------


def holds() -> list[dict]:
    """Every hold of every project: a call that is running, died or may have been billed (``project``, ``quote``,
    ``usd``, ``at``, ``status``, ``note``), from the central log. A line that cannot be read is skipped here, so the
    person can still answer for a hold when the log is damaged (``overview`` says how many lines were)."""
    return _holds_of(_read_log(central_log())[0])


def clear_hold(quote_id: str, *, billed: bool) -> dict:
    """The person's answer about a call that may have been billed, after checking the provider's dashboard: it was
    (the spend is kept at the held price, and the quote is used up for good) or it was not (the hold is released, and the
    quote may be tried again within ``max_tries_per_quote``). Written to the central log and the project's. Waits for a
    paid call that is running, so the answer cannot land in the middle of it (call it from the command line, not from
    code running in an event loop that a paid call shares: it would wait without letting that call go on).

    Returns:
        ``project``, ``quote_id``, ``usd`` (held) and ``billed``.

    Raises:
        SpendError: there is no hold for the quote.
    """
    with _paying_sync():
        hold = next((h for h in holds() if h["quote"] == quote_id), None)
        if hold is None:
            raise SpendError(f"no hold for quote {quote_id}; `hyperframes-mcp spend status` lists them")
        project = Path(hold["project"])
        return _answer(project, hold, quote_id, billed)


def _answer(project: Path, hold: dict, quote_id: str, billed: bool) -> dict:
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "usd": hold["usd"] if billed else 0.0,
        "provider": hold.get("provider"),
        "note": "cleared by the person: it was billed" if billed else "cleared by the person: it was not billed",
        "ref": None,
        "quote": quote_id,
        "attempt": int(hold.get("attempt", 1)),
        "status": "confirmed" if billed else "released",
    }
    _append_spend(project, entry, own=project.is_dir(), strict=False, project_id=hold.get("project_id"))
    return {"project": str(project), "quote_id": quote_id, "usd": hold["usd"], "billed": billed}


def overview(*, now: datetime | None = None) -> dict:
    """The spend of every project together: the limits in force, the variables, whether paid calls are frozen, the
    calls and dollars of the last hour and of today, the holds, and the number of lines of the central log that could
    not be read (while there is one, nothing can be quoted or confirmed). For ``hyperframes-mcp spend status``.

    Raises:
        ValueError: ``limits.yaml`` or a variable is malformed.
    """
    now = now or datetime.now(UTC)
    today = now.astimezone(UTC).date()
    lines, problems = _read_log(central_log())
    counted = _counted(lines)
    attempts = _attempts(lines)
    return {
        "limits": load_spend_limits().model_dump(),
        "variables_in_force": variables_in_force(),
        "frozen": frozen(),
        "data_folder": str(central_log().parent),
        "calls_last_hour": sum(1 for line in attempts if _when(line) >= now - timedelta(hours=1)),
        "calls_today": sum(1 for line in attempts if _when(line).astimezone(UTC).date() == today),
        "spent_today_usd": round(
            sum(line["usd"] for line in counted if _when(line).astimezone(UTC).date() == today), 6
        ),
        "holds": _holds_of(lines),
        "damaged_lines": len(problems),
    }
