"""Bounded download of an archival image file (``download_file`` takes any content types).

Limits: https only (the first URL and every redirect target, checked before
the request is made), a byte cap, a total deadline for the whole download
including redirects, an accepted content type (an image for ``download_image``), and a public
address on every hop (see ``public_transport``: a name that resolves to a local, private or
metadata address is refused).

This is written here rather than reusing ``stock/downloader.py`` because that
downloader takes a ``StockCandidate``, follows any redirect, and has no byte
cap or total deadline. It sends no credentials, only a User-Agent.

A 429 or 503 is waited out and tried again within the total deadline (see ``RetryingClient``).
"""

import asyncio
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from hyperframes_mcp.archival.base import (
    DEFAULT_USER_AGENT,
    RetryingClient,
    describe_error,
)
from hyperframes_mcp.archival.models import ArchivalCandidate
from hyperframes_mcp.util.public_transport import public_only_transport

logger = logging.getLogger(__name__)

_EXTENSIONS_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/tiff": ".tif",
}


class ArchivalDownloadError(Exception):
    """An archival file could not be downloaded within the limits."""


@dataclass(frozen=True)
class DownloadLimits:
    """Bounds on one download."""

    max_bytes: int = 40 * 1024 * 1024
    total_timeout: float = 120.0
    max_redirects: int = 5


DEFAULT_LIMITS = DownloadLimits()


def _require_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ArchivalDownloadError(f"refusing non-https URL: {url}")


def _safe_stem(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stem).lstrip(".")[:120] or "image"


async def _write_capped(response: httpx.Response, partial: Path, max_bytes: int) -> None:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise ArchivalDownloadError(f"declared size {declared} bytes exceeds the {max_bytes} byte cap")
    total = 0
    with partial.open("wb") as fh:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ArchivalDownloadError(f"download exceeds the {max_bytes} byte cap")
            fh.write(chunk)
    if total == 0:
        raise ArchivalDownloadError("empty response body")


async def download_file(
    url: str,
    dest_dir: Path,
    stem: str,
    *,
    content_types: Mapping[str, str],
    limits: DownloadLimits = DEFAULT_LIMITS,
    user_agent: str = DEFAULT_USER_AGENT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Download a file to ``dest_dir/<stem><ext>`` within ``limits``.

    ``content_types`` maps each accepted ``Content-Type`` (lower case, without
    parameters) to the extension the file gets; any other type is refused. The body
    is written to a ``.part`` file and renamed on success, so a failed download
    leaves nothing behind.

    Raises:
        ArchivalDownloadError: non-https URL or redirect target, too many
            redirects, HTTP error, a content type not in ``content_types``, byte
            cap exceeded, empty body, the total deadline passed, or the host kept
            answering 429 or 503 or asked for a wait over the retry limit.
    """
    _require_https(url)
    safe_stem = _safe_stem(stem)
    dest_dir.mkdir(parents=True, exist_ok=True)
    partial: Path | None = None
    try:
        async with (
            asyncio.timeout(limits.total_timeout),
            RetryingClient(
                headers={"User-Agent": user_agent},
                timeout=httpx.Timeout(min(30.0, limits.total_timeout)),
                follow_redirects=False,
                transport=transport or public_only_transport(),
            ) as client,
        ):
            current = url
            for _ in range(limits.max_redirects + 1):
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        current = str(response.url.join(response.headers["location"]))
                        _require_https(current)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    suffix = content_types.get(content_type)
                    if suffix is None:
                        raise ArchivalDownloadError(f"unsupported content type {content_type!r} from {current}")
                    dest = dest_dir / f"{safe_stem}{suffix}"
                    partial = dest.with_name(dest.name + ".part")
                    await _write_capped(response, partial, limits.max_bytes)
                    partial.replace(dest)
                    partial = None
                    logger.info("Downloaded %s to %s", url, dest)
                    return dest
            raise ArchivalDownloadError(f"more than {limits.max_redirects} redirects")
    except TimeoutError as exc:
        raise ArchivalDownloadError(f"download exceeded the {limits.total_timeout}s deadline") from exc
    except httpx.HTTPError as exc:
        raise ArchivalDownloadError(f"download of {url} failed: {describe_error(exc)}") from exc
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)


async def download_image(
    url: str,
    dest_dir: Path,
    stem: str,
    *,
    limits: DownloadLimits = DEFAULT_LIMITS,
    user_agent: str = DEFAULT_USER_AGENT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Download an image to ``dest_dir/<stem><ext>`` within ``limits``.

    The extension comes from the response ``Content-Type`` (JPEG, PNG, GIF, WebP or TIFF).

    Raises:
        ArchivalDownloadError: as ``download_file``, with a non-image content type refused.
    """
    return await download_file(
        url,
        dest_dir,
        stem,
        content_types=_EXTENSIONS_BY_CONTENT_TYPE,
        limits=limits,
        user_agent=user_agent,
        transport=transport,
    )


async def download_candidate(
    candidate: ArchivalCandidate,
    dest_dir: Path,
    *,
    limits: DownloadLimits = DEFAULT_LIMITS,
    user_agent: str = DEFAULT_USER_AGENT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Download the file behind ``candidate.direct_file_url``.

    The file is named ``<provider>_<provider_id><ext>``. This does not check
    rights: callers decide whether a candidate may be used.

    Raises:
        ArchivalDownloadError: the candidate has no direct file URL, or the
            download failed (see ``download_image``).
    """
    if not candidate.direct_file_url:
        raise ArchivalDownloadError(f"{candidate.provider} {candidate.provider_id} has no direct file URL")
    return await download_image(
        candidate.direct_file_url,
        dest_dir,
        f"{candidate.provider}_{candidate.provider_id}",
        limits=limits,
        user_agent=user_agent,
        transport=transport,
    )
