"""Free pictures for an edit in the desktop app: search the free archives, keep what was found, fetch one hit into the
project with its licence record.

The sources (``edit_config.SOURCES``) are the archival module's clients for the Library of Congress, Wikimedia Commons and
the Internet Archive, and Openverse's image search (``archival/openverse.py``); they search, rate-limit and recognise
licences as they do for the visual stage, and a hit is downloaded by the archival safe download (https only, a public
address on every hop, a byte cap, a deadline). Nothing is paid and no key is used. Those sources hold still pictures only:
the archival module has no video, so there is no clip search.

The licence policy is the sound module's: CC0 and public domain (the public-domain mark) by default, CC BY only when
asked (its credit text is required: a CC BY item with no creator to credit is never found), and share-alike,
non-commercial, no-derivatives and unrecognised licences never. A hit is also left out when it has no direct file, is under
``MIN_WIDTH`` px wide, or does not match the query's words (``service.relevance``). Hits are ranked within each source and the
sources then take turns in the profile's order, so a handful is not all from one.

The hits of a search are kept in ``<project>/.video-mcp/media_hits.json`` (the latest ``MAX_HITS_KEPT``), because a hit's
id has to find the file URL and licence record again for ``fetch``; the licence is checked again at the fetch. A fetched
picture is ``<project>/assets/media/found/<name>.<jpg|png|webp>`` with ``<file>.json`` beside it, which
``project_edit.replace_media`` reads for the credit.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image
from pydantic import ValidationError

from hyperframes_mcp.archival.attribution import PROVIDER_LABELS, credit_line
from hyperframes_mcp.archival.base import ArchivalProvider, load_user_agent
from hyperframes_mcp.archival.download import ArchivalDownloadError, download_file
from hyperframes_mcp.archival.internet_archive import InternetArchiveClient
from hyperframes_mcp.archival.loc import LibraryOfCongressClient
from hyperframes_mcp.archival.models import ArchivalCandidate, RightsStatus
from hyperframes_mcp.archival.openverse import OpenverseImageClient
from hyperframes_mcp.archival.rights import RIGHTS_ORDER
from hyperframes_mcp.archival.selection import (
    MIN_RELEVANCE,
    MIN_WIDTH,
    RESULTS_PER_PROVIDER,
    STOP_WORDS,
    relevance,
    unusable_reason,
)
from hyperframes_mcp.archival.textutil import strip_html
from hyperframes_mcp.archival.wikimedia import WikimediaCommonsClient
from hyperframes_mcp.motion.compose import MEDIA_DIR
from hyperframes_mcp.motion.spend import STATE_DIR
from hyperframes_mcp.util.atomic import atomic_write_bytes

FOUND_DIR = f"{MEDIA_DIR}/found"
HITS_FILE = "media_hits.json"
MAX_HITS_KEPT = 100
DEFAULT_LIMIT = 6
MAX_LIMIT = 10
SHOWN_TITLE_CHARS = 120
SOURCE_SECONDS = 45.0  # the longest one source may take; the others' hits are not held up past it
# a source that takes this long is named with its time in ``tried`` (the archival clients wait out a host's 429, so one
# rate-limited source holds the whole answer up: seen live, 2026-10-07, 25 s for a search that takes 0.4 s)
SLOW_SECONDS = 3.0
# a search that finds nothing usable is asked again with fewer words, but only while this many seconds of the first ask
# are not used up (a slow source must not make the whole call slower still), and at most this many more times
RETRY_WITHIN_S = 12.0
MAX_RETRIES = 2
CREDIT_CHARS = 140  # the credit-line block's ``text`` limit (assets/motion/blocks/credit-line.html)
# the credit-line block needs its entrance, 1.2 s and 0.3 s a word, and its exit: twelve words are 6 s on screen
CREDIT_WORDS = 12
# the creator a source gives when it has none ("Unknown author or not provided" on Wikimedia Commons)
_PLACEHOLDER_CREATOR = re.compile(
    r"(?:(?:unknown|anonymous)(?: (?:author|artist|photographer|creator))?(?: or not provided)?|author unknown|not provided)\.?",
    re.IGNORECASE,
)
# the file name a hit is saved under, without folder or suffix (the same rule as the video MCP server's other new files)
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# what ``project_edit.replace_media`` takes for a photo-shot; any other type the host sends is refused
FILE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_CLIENTS = {
    "loc": LibraryOfCongressClient,
    "wikimedia": WikimediaCommonsClient,
    "internet_archive": InternetArchiveClient,
    "openverse": OpenverseImageClient,
}
_BY = RightsStatus.ATTRIBUTION_REQUIRED
# why a hit is left out, in the order a search reports them
_LEFT_OUT = {
    "licence": "with a licence that cannot be used",
    "by": "CC BY not asked for",
    "file": f"with no usable file (none, or under {MIN_WIDTH} px wide)",
    "match": "not matching the words",
}
_WHY_NOT = {
    "licence": "its licence cannot be used on a monetised video (only CC0, public domain and, when asked, CC BY)",
    "by": "it is CC BY and the search did not ask for CC BY (search again with allow_by=true)",
    "file": f"it has no direct file, or one under {MIN_WIDTH} px wide",
}
TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests point this at a mock; None is the network


class MediaError(RuntimeError):
    """A search or fetch cannot go on; the message says why and what to do. Nothing is left behind."""


def build_providers(sources: Sequence[str], transport: httpx.AsyncBaseTransport | None) -> dict[str, ArchivalProvider]:
    """One client per source named, in that order, all with the archival User-Agent."""
    agent = load_user_agent()
    return {name: _CLIENTS[name](user_agent=agent, transport=transport) for name in sources}


def _refusal(candidate: ArchivalCandidate, allow_by: bool) -> str | None:
    """Why a hit may not be used (a key of ``_LEFT_OUT``), or None when it may."""
    if candidate.rights_status is _BY and not allow_by:
        return "by"
    if not candidate.usable_by_default:
        return "licence"
    return "file" if unusable_reason(candidate) else None


def hit_id(candidate: ArchivalCandidate) -> str:
    """A short id (8 hex digits) of the hit: the same picture always has the same one."""
    key = f"{candidate.provider}:{candidate.provider_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def licence_name(candidate: ArchivalCandidate) -> str:
    """The licence as a short name: ``Public domain``, ``CC0``, or for CC BY its id (``CC-BY-4.0``)."""
    if candidate.rights_status is RightsStatus.PUBLIC_DOMAIN:
        return "Public domain"
    if candidate.rights_status is RightsStatus.CC0:
        return "CC0"
    return candidate.licence_id or "CC BY"


def _host(candidate: ArchivalCandidate) -> str:
    return (urlparse(candidate.source_url).hostname or "").removeprefix("www.")


def source_label(candidate: ArchivalCandidate) -> str:
    """Where the picture is from, for a person: the archive, or for Openverse the collection's site."""
    if candidate.provider == "openverse":
        return f"{_host(candidate)} (Openverse)"
    return PROVIDER_LABELS.get(candidate.provider, candidate.provider)


def _cut(text: str, room: int, words: int | None = None) -> str:
    """``text`` in at most ``room`` characters (and ``words`` words, when given), an ellipsis where it was cut."""
    if len(text) <= room and (words is None or len(text.split()) <= words):
        return text
    kept = text.split() if words is None else text.split()[: max(words, 0)]
    cut = " ".join(kept)
    if len(cut) > room - 1:
        cut = cut[: max(room - 1, 0)]
    return cut.rstrip() + "…"


def _credited(candidate: ArchivalCandidate) -> str | None:
    """Who the screen credit names: the creator, unless that is the source's placeholder for none ("Unknown author or
    not provided"), then the source's own credit line when it has one (Wikimedia Commons' ``Credit``: the archive a
    photograph came from), else nobody."""
    who = " ".join((candidate.creator or "").split())
    if who and not _PLACEHOLDER_CREATOR.match(who):
        return who
    fields = getattr(candidate.provenance, "raw_rights_fields", None) or {}
    return strip_html(str(fields.get("Credit") or "")) or None


def screen_credit(candidate: ArchivalCandidate) -> str:
    """The credit for the screen, at most ``CREDIT_CHARS`` characters and ``CREDIT_WORDS`` words (the block needs time to
    read each word, so a long one would not fit an ordinary picture): ``Creator, Source. Licence.`` for public domain and
    CC0, which ask for no credit; ``"Title" by Creator, Source. Licence.`` for CC BY, which does; a picture with no one to
    name gets its title.

    The title is cut first, then dropped, so who made it and under which licence stay; a creator too long for the room is
    cut. The full line for the video description is the record's ``attribution`` (``attribution.credit_line``).
    """
    source = _host(candidate) if candidate.provider == "openverse" else None
    source = source or PROVIDER_LABELS.get(candidate.provider, candidate.provider)
    tail = f", {source}. {licence_name(candidate)}."
    room = CREDIT_CHARS - len(tail)
    room_words = CREDIT_WORDS - len(tail.split())
    who = _credited(candidate)
    if who and not candidate.rights_status.needs_credit:
        lead = _cut(who, room, room_words)
    elif who:
        title_room = room - len(f'"" by {who}')
        title_words = room_words - len(f"by {who}".split())
        lead = (
            f'"{_cut(candidate.title, title_room, title_words)}" by {who}'
            if title_room >= 15 and title_words >= 2
            else _cut(who, room, room_words)
        )
    else:
        lead = f'"{_cut(candidate.title, room - 2, room_words)}"'
    return lead + tail


def _view(candidate: ArchivalCandidate) -> dict:
    size = candidate.dimensions
    return {
        "id": hit_id(candidate),
        "title": _cut(" ".join(candidate.title.split()), SHOWN_TITLE_CHARS),
        "source": source_label(candidate),
        "licence": licence_name(candidate),
        "credit": screen_credit(candidate),
        "size": f"{size.width}x{size.height}" if size else None,
        "date": candidate.date,
    }


def _hits_path(project: Path) -> Path:
    return project / STATE_DIR / HITS_FILE


def _read_book(project: Path) -> dict[str, dict]:
    """The hits kept for ``project``; a missing or damaged file is no hits (a search makes them again, free)."""
    try:
        book = json.loads(_hits_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return book if isinstance(book, dict) else {}


def _remember(project: Path, entries: Mapping[str, dict]) -> None:
    """Add ``entries`` to the project's hits as the newest, keeping the latest ``MAX_HITS_KEPT``."""
    book = _read_book(project)
    for key, entry in entries.items():
        book.pop(key, None)
        book[key] = entry
    while len(book) > MAX_HITS_KEPT:
        del book[next(iter(book))]
    path = _hits_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, (json.dumps(book, indent=1) + "\n").encode("utf-8"))


def _failure(error: BaseException) -> str:
    """A short word for a person on why a call failed: the HTTP status when there is one, else the error's own text."""
    status = getattr(error, "status", None)
    return f"HTTP {status}" if status else (str(error) or type(error).__name__)[:120]


def loosened(query: str) -> list[str]:
    """Fewer-word forms of ``query`` for a search that found nothing, the likeliest first: only its names and years
    (``Marburg 1945``), then its first two content words. A source that wants every word finds more with fewer. A lone
    capitalised first word may only start the sentence ("Gold bars stacked in a vault"), so then the wider form goes
    first. Words the relevance check ignores (``STOP_WORDS``) are never kept. Empty for a query of two words or fewer,
    which has nothing to drop."""
    words = query.split()
    if len(words) <= 2:
        return []
    names = [w for w in words if (w[:1].isupper() or w.isdigit()) and w.lower() not in STOP_WORDS]
    content = [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]
    ordered = (content[:2], names) if names == words[:1] else (names, content[:2])
    forms: list[str] = []
    for kept in ordered:
        form = " ".join(kept)
        if kept and len(kept) < len(words) and form not in forms:
            forms.append(form)
    return forms


async def search(
    project: str | Path,
    query: str,
    *,
    sources: Sequence[str],
    allow_by: bool,
    limit: int = DEFAULT_LIMIT,
    providers: Mapping[str, ArchivalProvider] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Search ``sources`` (in that order) for pictures of ``query`` under the licence policy; keep up to ``limit`` hits.

    ``providers`` replaces the real clients (tests). Returns the ``query``, ``allow_by``, the ``licences`` searched, the
    ``hits`` (each with an ``id``, ``title``, ``source``, ``licence``, ``credit``, ``size`` and ``date``) and ``tried``, one
    line per source: how many it found, how many were usable and why the rest were left out, or why it failed, and how
    long it took when that was ``SLOW_SECONDS`` or more. A search with nothing usable is asked again with fewer words
    (``loosened``), up to ``MAX_RETRIES`` times and while ``RETRY_WITHIN_S`` seconds of the first ask are left, and only
    the sources that did not fail: their lines come after, each starting ``as '<words>':``, and ``retried`` lists the
    wordings asked.

    Raises:
        MediaError: the query is empty or ``limit`` is outside 1 to ``MAX_LIMIT``.
    """
    query = " ".join(query.split())
    if not query:
        raise MediaError("say what the picture shows")
    if not 1 <= limit <= MAX_LIMIT:
        raise MediaError(f"limit is from 1 to {MAX_LIMIT}")
    clients = dict(providers) if providers is not None else build_providers(sources, transport or TRANSPORT)

    async def attempt(
        text: str, names: Sequence[str]
    ) -> tuple[list[str], list[list[tuple[str, ArchivalCandidate]]], set[str]]:
        """One round: the sources ``names`` asked for ``text``; a line of ``tried`` for each, the usable hits, best first,
        and the sources that failed."""
        took: dict[str, float] = {}

        async def ask(name: str) -> list[ArchivalCandidate]:
            began = time.monotonic()
            try:
                return await asyncio.wait_for(clients[name].search(text, RESULTS_PER_PROVIDER), SOURCE_SECONDS)
            finally:
                took[name] = time.monotonic() - began

        answers = await asyncio.gather(*(ask(name) for name in names), return_exceptions=True)
        lines: list[str] = []
        found: list[list[tuple[str, ArchivalCandidate]]] = []
        failed: set[str] = set()
        for name, answer in zip(names, answers):
            slow = f" (took {took[name]:.0f} s)" if took[name] >= SLOW_SECONDS else ""
            if isinstance(answer, BaseException):
                failed.add(name)
                lines.append(f"{name}: failed ({_failure(answer)}){slow}")
                continue
            kept, left = [], Counter()
            for candidate in answer:
                if why := _refusal(candidate, allow_by):
                    left[why] += 1
                elif relevance(text, candidate) < MIN_RELEVANCE:
                    left["match"] += 1
                else:
                    kept.append(candidate)
            kept.sort(
                key=lambda c: (
                    -round(relevance(text, c), 2),
                    RIGHTS_ORDER[c.rights_status],
                    -(c.dimensions.pixels if c.dimensions else 0),
                )
            )
            found.append([(name, c) for c in kept])
            line = f"{name}: {len(answer)} found, {len(kept)} usable" if answer else f"{name}: nothing found"
            if left:
                line += "; left out: " + ", ".join(
                    f"{left[why]} {label}" for why, label in _LEFT_OUT.items() if left[why]
                )
            lines.append(line + slow)
        return lines, found, failed

    began = time.monotonic()
    used, retried = query, []
    try:
        tried, ranked, failed = await attempt(query, sources)
        asking = [name for name in sources if name not in failed]
        for again in loosened(query)[:MAX_RETRIES]:
            left = RETRY_WITHIN_S - (time.monotonic() - began)
            if any(ranked) or not asking or left <= 0:
                break
            try:
                lines, ranked, failed = await asyncio.wait_for(attempt(again, asking), left)
            except TimeoutError:
                tried.append(f"as {again!r}: not answered in the {RETRY_WITHIN_S:g} s allowed for asking again")
                break
            used = again
            retried.append(again)
            tried += [f"as {again!r}: {line}" for line in lines]
            asking = [name for name in asking if name not in failed]
    finally:
        await asyncio.gather(*(client.close() for client in clients.values()))

    shown = [pair for turn in zip_longest(*ranked) for pair in turn if pair is not None][:limit]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if shown:
        _remember(
            Path(project).resolve(),
            {
                hit_id(c): {
                    "candidate": c.model_dump(mode="json"),
                    "source": name,
                    "allow_by": allow_by,
                    "query": used,
                    "found_at": now,
                }
                for name, c in shown
            },
        )
    return {
        "query": query,
        "allow_by": allow_by,
        "licences": "CC0, public domain and CC BY" if allow_by else "CC0 and public domain",
        "hits": [_view(c) for _, c in shown],
        "tried": tried,
        "retried": retried,
    }


async def fetch(
    project: str | Path,
    found_id: str,
    name: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """Download the hit ``found_id`` of an earlier search into ``<project>/assets/media/found/<name>.<ext>`` and write its
    record (``<file>.json``: the on-screen ``credit``, the full ``attribution``, the ``licence``, ``source_url``,
    ``ai: false``, ``verify_at_source`` (always true: a source's licence is its own word, a person confirms it at the
    source page), the picture's size and sha256, and when). The licence is checked again first. Returns the file's
    ``path`` (relative to the project), ``credit``, ``licence``, ``source_url``, ``size`` and ``verify_at_source``.

    Raises:
        MediaError: the name is not a plain name or is taken, the id was never found (or is no longer kept), the licence
            does not allow it, the download failed within its limits, or the file is not a picture of at least
            ``MIN_WIDTH`` px; nothing is left behind.
    """
    project = Path(project).resolve()
    if not NAME.fullmatch(name):
        raise MediaError(f"name {name!r}: use letters, digits, - and _ only (up to 64), no folder or suffix")
    entry = _read_book(project).get(found_id)
    if entry is None:
        raise MediaError(
            f"no picture {found_id!r} among the latest {MAX_HITS_KEPT} found for this project: call search_media first"
        )
    try:
        candidate = ArchivalCandidate.model_validate(entry["candidate"])
        allow_by, query = bool(entry["allow_by"]), entry["query"]
    except (KeyError, ValidationError) as error:
        raise MediaError(
            f"the record of picture {found_id} cannot be read ({_failure(error)}): search again"
        ) from error
    if why := _refusal(candidate, allow_by):
        raise MediaError(f"{candidate.title!r} cannot be used: {_WHY_NOT[why]}")
    folder = project / FOUND_DIR
    if folder.is_dir() and any(p.name.startswith(f"{name}.") for p in folder.iterdir()):
        raise MediaError(f"{FOUND_DIR}/{name}.* is already a file: pick another name")
    try:
        path = await download_file(
            candidate.direct_file_url,
            folder,
            name,
            content_types=FILE_TYPES,
            user_agent=load_user_agent(),
            transport=transport or TRANSPORT,
        )
    except ArchivalDownloadError as error:
        raise MediaError(f"{candidate.title!r} could not be fetched: {error}; try another hit") from error
    try:
        with Image.open(path) as picture:
            width, height = picture.size
            picture.verify()
    except (OSError, Image.DecompressionBombError) as error:
        path.unlink(missing_ok=True)
        raise MediaError(
            f"{candidate.title!r} came as a file that cannot be read as a picture ({_failure(error)}); try another hit"
        ) from error
    if width < MIN_WIDTH:
        path.unlink(missing_ok=True)
        raise MediaError(f"{candidate.title!r} is only {width} px wide, too small for the frame; try another hit")
    credit = screen_credit(candidate)
    record = {
        "ai": False,
        "credit": credit,
        "attribution": credit_line(candidate),
        "licence": licence_name(candidate),
        "licence_id": candidate.licence_id,
        "licence_url": candidate.licence_url,
        "rights_status": candidate.rights_status.value,
        "title": candidate.title,
        "creator": candidate.creator,
        "date": candidate.date,
        "source": source_label(candidate),
        "source_url": candidate.source_url,
        "verify_at_source": True,
        "size": f"{width}x{height}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "query": query,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    atomic_write_bytes(
        path.with_name(path.name + ".json"),
        (json.dumps(record, indent=2) + "\n").encode("utf-8"),
    )
    return {
        "path": path.relative_to(project).as_posix(),
        "credit": credit,
        "licence": record["licence"],
        "source_url": candidate.source_url,
        "size": record["size"],
        "verify_at_source": True,
    }
