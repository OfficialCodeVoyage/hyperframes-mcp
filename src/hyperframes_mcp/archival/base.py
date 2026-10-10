"""Provider protocol and shared HTTP plumbing for archival clients."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from hyperframes_mcp.archival.models import ArchivalCandidate
from hyperframes_mcp.util.paths import config_path
from hyperframes_mcp.util.yaml_io import load_yaml

logger = logging.getLogger(__name__)

# Wikimedia's policy asks API clients to name themselves and give a contact (a URL or an address).
# This default is the project's repository; ``visuals.archival.user_agent`` in config/settings.yaml
# replaces it, and a client or the downloader takes its own ``user_agent`` too.
DEFAULT_USER_AGENT = (
    "hyperframes-mcp-archival/0.1 (https://github.com/OfficialCodeVoyage/hyperframes-mcp; read-only image search)"
)

_SETTINGS_PATH = config_path("settings.yaml")

# A host that answers 429 or 503 is asking for a pause: up to MAX_ATTEMPTS tries per request, waiting
# the host's Retry-After, or 1 s then 2 s without one. A Retry-After above MAX_RETRY_WAIT fails the
# request at once (Wikimedia sent 600 s): the cue falls back to another archive or stock, so at most
# 2 x MAX_RETRY_WAIT is ever slept per request. A download's total deadline covers the waits too.
RETRY_STATUSES = frozenset({429, 503})
MAX_ATTEMPTS = 3
MAX_RETRY_WAIT = 10.0

HttpParams = Mapping[str, str | int] | Sequence[tuple[str, str | int]]


class ArchivalProviderError(Exception):
    """A provider request failed or returned something that is not valid JSON.

    ``status`` is the HTTP status when the provider answered with an error status.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RetryAfterTooLong(httpx.HTTPError):
    """A host asked for a longer wait than ``MAX_RETRY_WAIT``; the request is not retried."""


@runtime_checkable
class ArchivalProvider(Protocol):
    """What every archival source implements."""

    name: str

    async def search(self, query: str, limit: int = 10) -> list[ArchivalCandidate]:
        """Search the source and return candidates with rights assessed."""
        ...

    async def close(self) -> None:
        """Release the HTTP client."""
        ...


def _archival_settings(settings_path: Path) -> Mapping[str, Any]:
    """The ``visuals.archival`` section of config/settings.yaml (empty when the file or section is missing)."""
    try:
        settings = load_yaml(settings_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        settings = {}
    return (settings.get("visuals") or {}).get("archival") or {}


def load_user_agent(settings_path: Path = _SETTINGS_PATH) -> str:
    """The User-Agent from ``visuals.archival.user_agent`` in config/settings.yaml.

    A missing file, section or key, or a blank value, gives ``DEFAULT_USER_AGENT``.
    """
    section = _archival_settings(settings_path)
    return str(section.get("user_agent") or "").strip() or DEFAULT_USER_AGENT


def load_archival_timeout(key: str, default: float, settings_path: Path | None = None) -> float:
    """Seconds from ``visuals.archival.<key>`` in config/settings.yaml (or in ``settings_path``).

    A missing file, section or key, or a value that is not a positive number, gives ``default``.
    """
    value = _archival_settings(settings_path or _SETTINGS_PATH).get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return default
    return float(value)


def load_archival_section(key: str, settings_path: Path | None = None) -> Mapping[str, Any]:
    """The mapping at ``visuals.archival.<key>`` in config/settings.yaml (or in ``settings_path``).

    A missing file, section or key, or a value that is not a mapping, gives an empty mapping.
    """
    value = _archival_settings(settings_path or _SETTINGS_PATH).get(key)
    return value if isinstance(value, Mapping) else {}


def describe_error(exc: BaseException) -> str:
    """The exception's type and text. httpx timeouts have no text, so the type is all there is."""
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def parse_retry_after(value: str | None) -> float | None:
    """Seconds to wait from a ``Retry-After`` value (whole seconds or an HTTP date), else None.

    A date in the past is 0.
    """
    value = (value or "").strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - utc_now()).total_seconds())


def _retry_delay(response: httpx.Response, attempt: int) -> float | None:
    """Seconds to sleep before trying again, or None when this answer is final.

    Raises:
        RetryAfterTooLong: the host's Retry-After is above ``MAX_RETRY_WAIT``.
    """
    if response.status_code not in RETRY_STATUSES:
        return None
    raw = response.headers.get("retry-after")
    asked = parse_retry_after(raw)
    if asked is not None and asked > MAX_RETRY_WAIT:
        raise RetryAfterTooLong(
            f"{response.request.url.host} answered {response.status_code} with "
            f"Retry-After: {raw} (a wait of {asked:.0f}s; the limit is {MAX_RETRY_WAIT:g}s)"
        )
    if attempt >= MAX_ATTEMPTS:
        return None
    return asked if asked is not None else float(2 ** (attempt - 1))


_sleep = asyncio.sleep  # a name of its own so the archival tests can replace it


class RetryingClient(httpx.AsyncClient):
    """An httpx client that waits and tries again when a host answers 429 or 503.

    ``get`` and ``stream`` both go through ``send``, so one override covers searches and
    downloads. A retry sends the same request again (these are all GETs). Waiting inside a
    request keeps the caller's semaphore, which slows the other requests to the same host too.
    """

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        attempt = 1
        while True:
            response = await super().send(request, **kwargs)
            try:
                wait = _retry_delay(response, attempt)
            except RetryAfterTooLong:
                await response.aclose()
                raise
            if wait is None:
                return response
            await response.aclose()
            logger.warning(
                "%s answered %d; retrying in %gs (attempt %d of %d)",
                request.url.host,
                response.status_code,
                wait,
                attempt + 1,
                MAX_ATTEMPTS,
            )
            await _sleep(wait)
            attempt += 1


def build_http_client(
    *,
    user_agent: str,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
) -> httpx.AsyncClient:
    """An HTTP client with a polite User-Agent and no credentials."""
    return RetryingClient(
        headers={"User-Agent": user_agent, "Accept": "application/json"},
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        transport=transport,
    )


async def fetch_json(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    params: HttpParams | None = None,
) -> tuple[Any, str]:
    """GET ``url`` and return ``(parsed_json, final_request_url)``.

    Raises:
        ArchivalProviderError: network error, non-2xx status or invalid JSON.
    """
    async with semaphore:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json(), str(response.request.url)
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            raise ArchivalProviderError(f"GET {url} failed: {describe_error(exc)}", status=status) from exc
        except ValueError as exc:
            raise ArchivalProviderError(f"GET {url} returned invalid JSON") from exc


def utc_now() -> datetime:
    """Current time, timezone-aware."""
    return datetime.now(UTC)
