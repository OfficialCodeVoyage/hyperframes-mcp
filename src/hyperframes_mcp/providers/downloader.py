"""Downloads the media file behind a stock search candidate.

Provider-agnostic: works from the StockCandidate contract every stock client
fills in (``source_url``, ``media_type``, ``provider``, ``provider_id``), so a
new provider needs no changes here. It uses its own HTTP client with no
credentials: provider API keys belong to the search clients and must not be
sent to a CDN host.

The URLs come from third-party APIs, so a download is bounded: https only (the
first URL and every redirect target), a byte cap per media type, and a total
deadline that covers the whole request, not just each socket read, and a public
address on every hop (``public_transport``). Limits come
from ``visuals.stock_download`` in config/settings.yaml.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import aiofiles
import httpx
import yaml

from hyperframes_mcp.providers.stock_models import StockCandidate
from hyperframes_mcp.util.paths import config_path
from hyperframes_mcp.util.public_transport import public_only_transport

logger = logging.getLogger(__name__)

_KNOWN_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png", ".webp"})

_SETTINGS_PATH = config_path("settings.yaml")
_MB = 1024 * 1024
DEFAULT_MAX_VIDEO_MB = 200
DEFAULT_MAX_PHOTO_MB = 25
DEFAULT_TOTAL_TIMEOUT_SECONDS = 300.0
MAX_REDIRECTS = 5


class StockDownloadError(Exception):
    """A stock media file could not be downloaded."""


@dataclass(frozen=True)
class DownloadLimits:
    """Bounds applied to every download."""

    max_video_bytes: int
    max_photo_bytes: int
    total_timeout: float


def _stock_download_settings(settings_path: Path) -> dict:
    """The ``visuals.stock_download`` section of config/settings.yaml (empty if absent)."""
    try:
        settings = yaml.safe_load(settings_path.read_text()) or {}
    except FileNotFoundError:
        settings = {}
    return (settings.get("visuals") or {}).get("stock_download") or {}


def load_storyblocks_enabled(settings_path: Path = _SETTINGS_PATH) -> bool:
    """Whether stock video search may use Storyblocks (``storyblocks_enabled``).

    Off unless settings turn it on: the Storyblocks client only returns
    watermarked preview URLs, which are never downloaded, so searching it would
    spend calls and rank candidates nobody can use.
    """
    return bool(_stock_download_settings(settings_path).get("storyblocks_enabled", False))


def load_download_limits(settings_path: Path = _SETTINGS_PATH) -> DownloadLimits:
    """Read ``visuals.stock_download`` from config/settings.yaml.

    Keys: ``max_video_mb``, ``max_photo_mb``, ``timeout_seconds``. Missing file
    or keys fall back to the defaults (200 MB video, 25 MB photo, 300 s).
    """
    section = _stock_download_settings(settings_path)
    return DownloadLimits(
        max_video_bytes=max(1, int(float(section.get("max_video_mb", DEFAULT_MAX_VIDEO_MB)) * _MB)),
        max_photo_bytes=max(1, int(float(section.get("max_photo_mb", DEFAULT_MAX_PHOTO_MB)) * _MB)),
        total_timeout=max(1.0, float(section.get("timeout_seconds", DEFAULT_TOTAL_TIMEOUT_SECONDS))),
    )


def require_https(url: str) -> None:
    """Refuse any other scheme. The message names the host, never the query."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise StockDownloadError(f"Refusing non-https URL (scheme {parsed.scheme!r}, host {parsed.hostname!r})")


async def _require_https_request(request: httpx.Request) -> None:
    """httpx request hook: runs for the first request and for every redirect hop."""
    require_https(str(request.url))


class StockDownloader:
    """Streams a candidate's ``source_url`` to a local file."""

    def __init__(
        self,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        max_video_bytes: int | None = None,
        max_photo_bytes: int | None = None,
        total_timeout: float | None = None,
    ) -> None:
        """Create a downloader.

        Args:
            timeout: httpx per-operation timeout (connect, each read, write).
            transport: Replaces the network, for tests.
            max_video_bytes: Byte cap for videos; default from settings.
            max_photo_bytes: Byte cap for photos; default from settings.
            total_timeout: Deadline in seconds for one whole download; default
                from settings.
        """
        limits = load_download_limits()
        self._timeout = timeout
        self._transport = transport
        self._max_video_bytes = limits.max_video_bytes if max_video_bytes is None else max_video_bytes
        self._max_photo_bytes = limits.max_photo_bytes if max_photo_bytes is None else max_photo_bytes
        self._total_timeout = limits.total_timeout if total_timeout is None else total_timeout

    async def download(self, candidate: StockCandidate, dest_dir: Path) -> Path:
        """Download ``candidate.source_url`` into ``dest_dir``.

        The file is named ``<provider>_<provider_id><ext>``. It is written to a
        temporary ``.part`` file and renamed on success, so a failed,
        oversized, timed-out or interrupted download never leaves a truncated
        file behind: the ``.part`` file is removed on every failure.

        Directory creation, the rename and the cleanup are single metadata
        calls and run on the event loop; the body is written through aiofiles,
        so a large clip does not block the loop.

        Returns:
            Path of the downloaded file.

        Raises:
            StockDownloadError: No source URL, a non-https URL or redirect, the
                size cap or total deadline exceeded, or the HTTP request failed.
        """
        if not candidate.source_url:
            raise StockDownloadError(f"{candidate.provider} {candidate.provider_id} has no source_url")
        require_https(candidate.source_url)

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{self._stem(candidate)}{self._extension(candidate)}"
        partial = dest.with_name(dest.name + ".part")
        label = f"{candidate.provider} {candidate.provider_id}"

        try:
            async with asyncio.timeout(self._total_timeout):
                await self._fetch(candidate, partial, self._byte_limit(candidate))
            partial.replace(dest)
        except TimeoutError as exc:
            raise StockDownloadError(f"Download of {label} timed out after {self._total_timeout:g}s") from exc
        except httpx.HTTPError as exc:
            raise StockDownloadError(f"Download failed for {label}: {exc}") from exc
        finally:
            partial.unlink(missing_ok=True)

        logger.info("Downloaded %s to %s", candidate.source_url, dest)
        return dest

    async def _fetch(self, candidate: StockCandidate, partial: Path, limit: int) -> None:
        """Stream the body into ``partial``, refusing anything over ``limit`` bytes."""
        label = f"{candidate.provider} {candidate.provider_id}"
        async with (
            httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                transport=self._transport or public_only_transport(),
                event_hooks={"request": [_require_https_request]},
            ) as client,
            client.stream("GET", candidate.source_url) as response,
        ):
            response.raise_for_status()
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise StockDownloadError(f"{label} declares {declared} bytes, over the {limit}-byte limit")
            written = 0
            async with aiofiles.open(partial, "wb") as fh:
                async for chunk in response.aiter_bytes():
                    written += len(chunk)
                    if written > limit:
                        raise StockDownloadError(f"{label} is larger than the {limit}-byte limit")
                    await fh.write(chunk)

    def _byte_limit(self, candidate: StockCandidate) -> int:
        """Videos are far bigger than photos, so each media type has its own cap."""
        if candidate.media_type == "video":
            return self._max_video_bytes
        return self._max_photo_bytes

    @staticmethod
    def _stem(candidate: StockCandidate) -> str:
        """File stem built from provider ids, safe against path separators."""
        return re.sub(r"[^A-Za-z0-9_.-]", "_", f"{candidate.provider}_{candidate.provider_id}")

    @staticmethod
    def _extension(candidate: StockCandidate) -> str:
        """Extension from the URL path, else a default for the media type."""
        suffix = PurePosixPath(urlparse(candidate.source_url).path).suffix.lower()
        if suffix in _KNOWN_EXTENSIONS:
            return suffix
        return ".mp4" if candidate.media_type == "video" else ".jpg"
