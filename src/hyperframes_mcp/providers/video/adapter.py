"""I2VAdapter.generate_clip: one still plus a motion prompt in, one ``.mp4`` out, capped and costed.

Per call, in order:

1. The model must be in the priced table and the length one it supports (else refused, nothing
   counted). The still must exist, be png/jpg/webp and fit ``max_still_mb``; it is sent inline as
   a base64 data URI. UNVERIFIED: fal's pages could not be fetched on whether every I2V endpoint
   accepts a data URI; if one answers 422 ``file_download_error`` or ``image_load_error``, give the
   adapter an ``image_url_for`` that uploads the still and returns an https URL.
2. The ``ClipBudget`` cap: 6 clips, attempts per clip, seconds, spend. A refusal makes no provider call.
3. The job runs on fal's queue (``FalVideoClient``) and the result is downloaded with the stock
   downloader's limits: https only (also on redirects), a byte cap, a total deadline, no
   credentials sent to the CDN. The file lands at ``<visuals_root>/<topic>/i2v/fal_<clip>-a<n>.mp4``
   so the render engine, which decides video or still by extension, reads it as video.
4. Every attempt that fal accepted is charged at the estimate in the clip cap and, with a
   a success, and also a content rejection, timeout or failed download after acceptance, because fal's
   docs do not say a failed job is free. An attempt fal rejected at submit counts as an attempt but costs nothing. A submit
   whose outcome is unknown (a 5xx, or an answer that was lost) may have created a job, so it is
   charged like an accepted one and is never sent again (``FalSubmitUncertainError``): a second POST
   could create a second billed job that nothing records. The ledger therefore errs high; reconcile
   it with the fal invoice.

The API key stays inside ``FalVideoClient``; no message here includes it.
"""

import asyncio
import base64
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from hyperframes_mcp.providers.downloader import StockDownloader, StockDownloadError
from hyperframes_mcp.providers.image.fal_client import (
    FalAPIError,
    FalError,
    FalNotSentError,
    FalTimeoutError,
)
from hyperframes_mcp.providers.image.providers import get_provider
from hyperframes_mcp.providers.stock_models import StockCandidate
from hyperframes_mcp.providers.video.budget import ClipBudget
from hyperframes_mcp.providers.video.errors import (
    ClipDownloadError,
    ClipGenerationError,
    ClipTimeoutError,
    ContentPolicyError,
    I2VError,
    MissingKeyError,
    StillError,
)
from hyperframes_mcp.providers.video.fal_video import FalJobError, FalVideoClient
from hyperframes_mcp.providers.video.pricing import estimate_clip_cost, get_model
from hyperframes_mcp.providers.video.settings import I2VSettings, load_i2v_settings

LEDGER_PROVIDER = "fal"
LEDGER_VISUAL_TYPE = "ai_video"
_MB = 1024 * 1024
_STILL_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_TIMEOUT_ERROR_TYPES = frozenset({"request_timeout", "startup_timeout", "generation_timeout"})
_POLICY_ERROR_TYPE = "content_policy_violation"

ImageUrlFor = Callable[[Path], Awaitable[str]]


@dataclass(frozen=True)
class ClipResult:
    """A downloaded clip and what it cost."""

    path: Path
    clip_id: str
    attempt: int
    model: str
    seconds: int
    cost_usd: float
    latency_seconds: float
    request_id: str


def build_fal_client(settings: I2VSettings | None = None, env: Mapping[str, str] | None = None) -> FalVideoClient:
    """A client keyed from the environment variable G1's ``fal`` provider reads.

    Raises:
        I2VError: that variable is not set.
    """
    settings = settings or load_i2v_settings()
    key_env = get_provider("fal").api_key_env
    key = (env if env is not None else os.environ).get(key_env)
    if not key:
        raise MissingKeyError(f"Set {key_env} (environment or .env) to call fal.ai")
    return FalVideoClient(
        key,
        poll_interval=settings.queue.poll_interval_seconds,
        timeout=settings.queue.job_timeout_seconds,
    )


class I2VAdapter:
    """Generates capped, costed I2V clips through fal.ai."""

    def __init__(
        self,
        client: FalVideoClient,
        budget: ClipBudget,
        *,
        settings: I2VSettings | None = None,
        visuals_root: str | Path,
        download_transport: httpx.AsyncBaseTransport | None = None,
        image_url_for: ImageUrlFor | None = None,
    ) -> None:
        self._client = client
        self._budget = budget
        self._settings = settings or load_i2v_settings()
        self._visuals_root = Path(visuals_root)
        self._download_transport = download_transport
        self._image_url_for = image_url_for or self._data_uri

    async def generate_clip(
        self,
        still_path: str | Path,
        motion_prompt: str,
        model: str,
        seconds: int,
        *,
        topic_id: int,
        clip_id: str | None = None,
    ) -> ClipResult:
        """Animate ``still_path`` for ``seconds`` on ``model``.

        Args:
            still_path: The still (png, jpg or webp).
            motion_prompt: What should move, for example "slow push-in, smoke drifts left".
            model: A key or endpoint id from ``I2V_MODELS``.
            seconds: A length the model supports.
            topic_id: The video this clip belongs to; the cap is per video.
            clip_id: Names the clip; calling again with the same id is a retry (another
                attempt). Defaults to the still's file stem.

        Raises:
            UnknownModelError, ClipLengthError, StillError: nothing was counted or sent.
            ClipCapError: the clip cap refused (7th clip, attempts, seconds, spend).
            ContentPolicyError, ClipTimeoutError, ClipGenerationError, ClipDownloadError:
                the attempt ran and failed; it is counted (see the module docstring).
        """
        spec = get_model(model)
        estimate = estimate_clip_cost(spec.key, seconds)
        still = Path(still_path)
        clip_id = clip_id or still.stem
        image_url = await self._image_url_for(still)

        attempt = await self._budget.begin_attempt(topic_id, clip_id, spec.key, seconds, estimate)

        accepted: list[str] = []
        started = time.monotonic()
        try:
            job = await self._client.run(
                spec.endpoint,
                spec.build_input(image_url, motion_prompt, seconds),
                on_accepted=accepted.append,
            )
            path = await self._download(_video_url(job.output), topic_id, f"{clip_id}-a{attempt.number}")
        except asyncio.CancelledError as exc:
            exc.may_be_billed = True  # the request may be on its way or running; the caller must hold its price
            await self._close(attempt.id, topic_id, estimate, bool(accepted))
            raise
        except Exception as exc:
            may_be_billed = not _refused_at_submit(exc, accepted)
            await self._close(attempt.id, topic_id, estimate, may_be_billed)
            translated = _translate(exc)
            # the caller's spend guard asks the error whether it can have been billed (a policy failure found after
            # fal accepted the job is billed; one found at submit is not)
            exc.may_be_billed = translated.may_be_billed = may_be_billed
            if translated is exc:
                raise
            raise translated from exc

        await self._budget.finish_attempt(attempt.id, "ok")
        return ClipResult(
            path=path,
            clip_id=clip_id,
            attempt=attempt.number,
            model=spec.key,
            seconds=seconds,
            cost_usd=estimate,
            latency_seconds=time.monotonic() - started,
            request_id=accepted[0],
        )

    async def _close(self, attempt_id: int, topic_id: int, estimate: float, may_be_billed: bool) -> None:
        """End a failed attempt: charged if fal accepted the job or may have, free if it never did."""
        if not may_be_billed:
            await self._budget.finish_attempt(attempt_id, "rejected")
            return
        await self._budget.finish_attempt(attempt_id, "failed")

    async def _download(self, url: str, topic_id: int, stem: str) -> Path:
        download = self._settings.download
        candidate = StockCandidate(
            provider=LEDGER_PROVIDER,
            provider_id=stem,
            media_type="video",
            title="",
            thumbnail_url="",
            source_url=url,
            width=0,
            height=0,
            photographer="",
        )
        downloader = StockDownloader(
            transport=self._download_transport,
            max_video_bytes=int(download.max_clip_mb * _MB),
            total_timeout=download.timeout_seconds,
        )
        return await downloader.download(candidate, self._visuals_root / str(topic_id) / "i2v")

    async def _data_uri(self, still: Path) -> str:
        return await asyncio.to_thread(_still_as_data_uri, still, int(self._settings.max_still_mb * _MB))


def _still_as_data_uri(still: Path, max_bytes: int) -> str:
    mime = _STILL_MIME.get(still.suffix.lower())
    if mime is None:
        raise StillError(f"{still.name}: use a png, jpg or webp still")
    try:
        data = still.read_bytes()
    except OSError as err:
        raise StillError(f"Cannot read the still {still}: {err.strerror or err}") from err
    if len(data) > max_bytes:
        raise StillError(
            f"{still.name} is {len(data) / _MB:.1f} MB; the limit is {max_bytes / _MB:g} MB. Downscale it first."
        )
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _refused_at_submit(exc: Exception, accepted: list[str]) -> bool:
    """Whether a failed attempt is known not to have been billed: fal read the submit and refused it with a 4xx status
    (a policy refusal, a bad key), before it accepted a job. Anything else may have created a job: a lost answer, a 5xx,
    an answer that is not a job, a failure of the job, a timeout, a download."""
    if accepted or isinstance(exc, FalJobError):
        return False
    if isinstance(exc, FalNotSentError):  # a 429, or a connection that was never made: fal cannot have taken it
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(exc, FalAPIError) and isinstance(status, int) and 400 <= status < 500 and status != 408


def _video_url(output: dict) -> str:
    url = (output.get("video") or {}).get("url")
    if not url:
        raise ClipGenerationError(f"fal returned no video: {str(output)[:300]}")
    return url


def _translate(exc: Exception) -> Exception:
    """A provider or download failure as the adapter's own error; others come back unchanged."""
    if isinstance(exc, I2VError):
        return exc
    if isinstance(exc, FalTimeoutError):
        return ClipTimeoutError(str(exc))
    if isinstance(exc, FalJobError):
        if exc.error_type == _POLICY_ERROR_TYPE:
            return ContentPolicyError(str(exc))
        if exc.error_type in _TIMEOUT_ERROR_TYPES:
            return ClipTimeoutError(str(exc))
        return ClipGenerationError(f"{exc} (error_type {exc.error_type})")
    if isinstance(exc, FalAPIError):
        if _POLICY_ERROR_TYPE in str(exc):
            return ContentPolicyError(str(exc))
        return ClipGenerationError(str(exc))
    if isinstance(exc, FalError):
        return ClipGenerationError(str(exc))
    if isinstance(exc, StockDownloadError):
        return ClipDownloadError(str(exc))
    return exc
