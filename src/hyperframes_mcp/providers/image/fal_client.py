"""FalImageClient -- async text-to-image through fal.ai's queue HTTP API (the "fal" provider).

fal hosts the models behind one API: FLUX.2 [pro], Seedream and OpenAI's gpt-image family. The
model is a fal endpoint id from ``visuals.ai_image.model`` in config/settings.yaml.

Facts below were read on 2026-09-30 from fal's pages; items marked UNVERIFIED were not.

Queue API (https://fal.ai/docs/model-apis/model-endpoints/queue)::

    POST https://queue.fal.run/{endpoint}            Authorization: Key $FAL_KEY, JSON input
         -> {request_id, response_url, status_url, cancel_url, queue_position}
    GET  {status_url}     -> {"status": "IN_QUEUE" | "IN_PROGRESS" | "COMPLETED", ...}
    GET  {response_url}   -> the model output (the status response does not carry it)

The client follows the ``status_url`` and ``response_url`` fal returns rather than rebuilding
them. The key is read from ``FAL_KEY`` by ``providers.create_image_client``; the variable name
is taken from the docs' curl examples (``$FAL_KEY``). UNVERIFIED: the error body fal returns
for a failed job (the client raises on a non-2xx result or an ``error`` key on the status).

Per-model input (``build_fal_input``), all with ``image_size`` as ``{"width", "height"}``:

* ``fal-ai/flux-2-pro`` (fal.ai/models/fal-ai/flux-2-pro/api): ``output_format`` jpeg|png,
  ``image_size`` presets or a custom width/height object; output ``images[].url``. UNVERIFIED:
  the minimum and maximum custom size.
* ``openai/gpt-image-2.5/{flare,sunburst}/text-to-image`` (fal.ai/models/openai/gpt-image-2.5/
  flare/text-to-image/api): ``quality`` auto|low|medium|high|xhigh|max, ``output_format``
  jpeg|png|webp, custom size with multiples of 16, at most 3840 px an edge, 655,360 to
  8,294,400 pixels. ``openai/gpt-image-2`` takes ``quality`` auto|low|medium|high.
* ``fal-ai/bytedance/seedream/v5/lite/text-to-image``: no quality or output format; the custom
  size must total at least 2560x1440 pixels, so a 1024x1024 preset is rejected (HTTP 422).
  ``fal-ai/bytedance/seedream/v4.5/text-to-image`` needs 3.7 to 16.8 megapixels.
* ``fal-ai/nano-banana-2`` (fal.ai/models/fal-ai/nano-banana-2/api, read 2026-10-10): no ``image_size``; it takes an
  ``aspect_ratio`` (a fixed list, ``auto`` by default) and a ``resolution`` (0.5K, 1K, 2K, 4K, 1K by default) and
  ``output_format`` jpeg|png|webp. The client sends the aspect ratio nearest the preset's size and the resolution its
  long edge falls in (``nano_banana_resolution``); the price table lists the sizes of each tier.
* Any other endpoint gets the generic ``prompt`` + ``image_size`` input. UNVERIFIED for such
  endpoints; check the model's API page before relying on it.

The POST that creates a job is not idempotent (fal documents no idempotency key). It is repeated only
when fal cannot have taken it: HTTP 429, or a connection that was never made (``FalNotSentError``). A
5xx or a lost response (a read timeout, a closed connection) may come after fal queued the job, so it is
raised once as ``FalSubmitUncertainError`` for the caller to charge at its estimate; a second POST would
create a second billed job whose request id nobody holds. Status and result reads are safe to repeat and
are retried on any transient failure.

Prices are in ``pricing.py`` / settings.yaml. Cost per image is an estimate, not fal's invoice.
"""

import asyncio
import logging
import math
import time
from contextlib import nullcontext
from io import BytesIO
from typing import Any

import httpx
from circuitbreaker import circuit
from PIL import Image
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from hyperframes_mcp.providers.bounded_fetch import get_bytes
from hyperframes_mcp.providers.downloader import StockDownloadError
from hyperframes_mcp.providers.image.models import AIImageResult
from hyperframes_mcp.providers.image.settings import ImageSettings

logger = logging.getLogger(__name__)

PROVIDER_NAME = "fal"
QUEUE_BASE = "https://queue.fal.run"
# The key is sent only to these: fal returns the status and result URLs, and a URL taken from a
# response must not make the client post the key somewhere else.
_FAL_HOSTS = ("fal.run", "fal.ai")
_PENDING_STATES = ("IN_QUEUE", "IN_PROGRESS")
# Transport failures where no request reached fal: the connection or a pooled slot was never obtained.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class FalError(Exception):
    """Base class for fal.ai failures."""


class FalAPIError(FalError):
    """fal.ai refused or failed the request; retrying will not help (4xx, failed job)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class FalTransientError(FalError):
    """A failure worth retrying: a dropped connection or HTTP 429."""


class FalNotSentError(FalTransientError):
    """fal cannot have taken the request: HTTP 429, or the connection was never made.

    Safe to repeat even for the POST that creates a job.
    """


class FalServerError(FalTransientError):
    """HTTP 5xx; only these open the circuit breaker."""


class FalSubmitUncertainError(FalServerError):
    """The POST that creates a job failed in a way that may have created it.

    A 5xx or a lost response (read timeout, closed connection) can follow a queued job whose request
    id was never seen. It is not retried, because a retry would create a second billed job; the caller
    charges the estimate of one possible job. A ``FalServerError``, so the circuit breaker counts it.
    """


class FalTimeoutError(FalError):
    """The job did not finish within the client's timeout."""


# The aspect ratios fal-ai/nano-banana-2 accepts besides ``auto``.
_NANO_BANANA_RATIOS = (
    (21, 9), (16, 9), (3, 2), (4, 3), (5, 4), (1, 1), (4, 5), (3, 4), (2, 3), (9, 16), (4, 1), (1, 4), (8, 1), (1, 8),
)  # fmt: skip
# The long edge up to which each resolution tier is drawn: a 16:9 picture is 1376 px (1K) or 2752 px (2K) wide.
_NANO_BANANA_TIERS = ((1536, "1K"), (2816, "2K"), (5632, "4K"))


def nano_banana_aspect_ratio(width: int, height: int) -> str:
    """The accepted aspect ratio nearest ``width`` x ``height`` (by the ratio of their sides), as ``"16:9"``."""
    wanted = math.log(width / height)
    w, h = min(_NANO_BANANA_RATIOS, key=lambda ratio: abs(math.log(ratio[0] / ratio[1]) - wanted))
    return f"{w}:{h}"


def nano_banana_resolution(width: int, height: int) -> str:
    """The resolution tier (``"1K"``, ``"2K"`` or ``"4K"``) a ``width`` x ``height`` picture is drawn at."""
    edge = max(width, height)
    return next((tier for limit, tier in _NANO_BANANA_TIERS if edge <= limit), _NANO_BANANA_TIERS[-1][1])


def build_fal_input(model: str, prompt: str, width: int, height: int, quality: str) -> dict[str, Any]:
    """The JSON input for ``model``; see the module docstring for what is verified."""
    if model == "fal-ai/nano-banana-2":
        return {
            "prompt": prompt,
            "aspect_ratio": nano_banana_aspect_ratio(width, height),
            "resolution": nano_banana_resolution(width, height),
            "output_format": "png",
        }
    body: dict[str, Any] = {
        "prompt": prompt,
        "image_size": {"width": width, "height": height},
    }
    if model.startswith("openai/gpt-image"):
        body["quality"] = quality
        body["output_format"] = "png"
    elif model.startswith("fal-ai/flux-2"):
        body["output_format"] = "png"
    return body


class FalImageClient:
    """Async fal.ai image generation with a semaphore, retries and a circuit breaker.

    Same interface as ``AIImageClient``. A status or result read is retried up to three times
    on a dropped connection, 429 or 5xx; the submit POST only when fal cannot have taken it (429,
    a connection never made), and a 5xx or lost response on it is raised once as
    ``FalSubmitUncertainError``. A 4xx is raised at once. Three failed images in a row with 5xx
    errors open the breaker for two minutes. Graceful degradation when the API key is missing:
    ``generate`` returns None.
    """

    def __init__(
        self,
        api_key: str | None,
        settings: ImageSettings,
        max_concurrent: int = 3,
        *,
        http_client: httpx.AsyncClient | None = None,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
        retry_wait: float = 2.0,
    ) -> None:
        if settings.provider != PROVIDER_NAME:
            raise ValueError(f"FalImageClient serves provider {PROVIDER_NAME!r}, not {settings.provider!r}")
        self._settings = settings
        self.provider_name = PROVIDER_NAME
        self.model = settings.model
        self._api_key = api_key
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._http_client = http_client
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._retry_wait = retry_wait

    async def generate(self, prompt: str, preset: str = "draft") -> AIImageResult | None:
        """Generate one image.

        Args:
            prompt: The generation prompt.
            preset: Preset name from the settings; an unknown name means "draft".

        Returns:
            AIImageResult with PNG bytes, measured dimensions and the estimated cost. None
            if no API key is configured.

        Raises:
            FalAPIError, FalServerError (``FalSubmitUncertainError`` when a job may exist),
            FalTimeoutError, CircuitBreakerError.
        """
        if not self._api_key:
            logger.warning("fal.ai not configured (no API key), returning None")
            return None

        async with self._semaphore:
            return await self._run_job(prompt, preset)

    @circuit(
        failure_threshold=3,
        recovery_timeout=120,
        expected_exception=FalServerError,
        name="fal_image",
    )
    async def _run_job(self, prompt: str, preset: str) -> AIImageResult:
        """Circuit-breaker-protected job: submit, poll, fetch the result, download."""
        config = self._settings.preset(preset)
        body = build_fal_input(self.model, prompt, config.width, config.height, config.quality)
        # An injected client belongs to the caller; otherwise open one for this job.
        async with (
            nullcontext(self._http_client)
            if self._http_client is not None
            else httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True)
        ) as http:
            try:
                submit = (await self._request(http, "POST", f"{QUEUE_BASE}/{self.model}", json=body)).json()
            except FalNotSentError as exc:
                exc.may_be_billed = False  # a 429 or a connection never made: fal cannot have taken the job
                raise
            if not isinstance(submit, dict) or not submit.get("status_url") or not submit.get("response_url"):
                raise FalSubmitUncertainError(
                    f"fal answered the submit with something that is not a job: {str(submit)[:200]}"
                )
            try:
                downloaded = await self._finish_job(http, submit)
            except Exception as exc:
                exc.may_be_billed = True  # fal accepted the job: whatever fails from here on, it may be billed
                raise

        # Decoding and re-encoding are CPU work; keep them off the event loop.
        image_bytes, (width, height) = await asyncio.to_thread(_as_png, downloaded)
        return AIImageResult(
            image_bytes=image_bytes,
            width=width,
            height=height,
            quality=config.quality,
            cost_usd=self._settings.unit_cost(preset),
            model=self.model,
            provider=PROVIDER_NAME,
        )

    async def _finish_job(self, http: httpx.AsyncClient, submit: dict) -> bytes:
        """Wait for the accepted job, read its result and download the first image."""
        await self._wait_until_complete(http, submit["status_url"])
        result = (await self._request(http, "GET", submit["response_url"])).json()
        images = result.get("images") or []
        if not images or not images[0].get("url"):
            raise FalAPIError(f"fal returned no image: {str(result)[:300]}")
        try:
            # The CDN URL comes from the result: https only, byte cap, deadline, no key.
            return await get_bytes(http, images[0]["url"])
        except StockDownloadError as exc:
            raise FalAPIError(f"fal image download refused: {exc}") from exc

    async def _wait_until_complete(self, http: httpx.AsyncClient, status_url: str) -> None:
        deadline = time.monotonic() + self._timeout
        while True:
            status = (await self._request(http, "GET", status_url)).json()
            if status.get("error"):
                raise FalAPIError(f"fal job failed: {status['error']}")
            state = status.get("status")
            if state == "COMPLETED":
                return
            if state not in _PENDING_STATES:
                raise FalAPIError(f"Unexpected fal job status {state!r}")
            if time.monotonic() >= deadline:
                raise FalTimeoutError(f"fal job still {state} after {self._timeout:.0f}s")
            await asyncio.sleep(self._poll_interval)

    async def _request(
        self,
        http: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        json: dict | None = None,
    ) -> httpx.Response:
        """One HTTP call, retried when that cannot create a second job. The key goes to fal's queue only."""
        _require_fal_url(url)
        headers = {"Authorization": f"Key {self._api_key}"}
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(lambda exc: _safe_to_repeat(method, exc)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=self._retry_wait, max=30),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        ):
            with attempt:
                return await _send(http, method, url, json, headers)


def _safe_to_repeat(method: str, exc: BaseException) -> bool:
    """Whether a failed call may be sent again: always when fal never took it, otherwise only for
    reads (a repeated POST to the queue is a second job)."""
    if isinstance(exc, FalNotSentError):
        return True
    return method.upper() != "POST" and isinstance(exc, FalTransientError)


def _require_fal_url(url: str) -> None:
    """Refuse to send the fal key to a URL that is not https on a fal.run or fal.ai host.

    Raises:
        FalAPIError: naming the host (never the key or the query).
    """
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise FalAPIError("refusing to send the fal key to an invalid URL") from None
    host = parsed.host.lower().rstrip(".")
    on_fal = host in _FAL_HOSTS or host.endswith(tuple("." + h for h in _FAL_HOSTS))
    if parsed.scheme != "https" or not on_fal or "@" in parsed.netloc.decode():
        raise FalAPIError(f"refusing to send the fal key to {parsed.scheme}://{host}")


async def _send(http: httpx.AsyncClient, method: str, url: str, json: dict | None, headers: dict) -> httpx.Response:
    submitting = method.upper() == "POST"
    try:
        response = await http.request(method, url, json=json, headers=headers)
    except httpx.TransportError as exc:
        if isinstance(exc, _NOT_SENT):
            raise FalNotSentError(f"fal request not sent: {exc!r}") from exc
        if submitting:
            raise FalSubmitUncertainError(
                f"fal submit may have created a job ({exc!r}); not repeated, so a second billed job is not created"
            ) from exc
        raise FalTransientError(f"fal request failed: {exc!r}") from exc
    status = response.status_code
    if status < 400:
        return response
    detail = response.text[:300]
    if status == 429:
        raise FalNotSentError(f"fal rate limit (HTTP 429): {detail}")
    if status >= 500:
        if submitting:
            raise FalSubmitUncertainError(
                f"fal submit may have created a job (HTTP {status}): {detail}; not repeated, "
                "so a second billed job is not created"
            )
        raise FalServerError(f"fal server error (HTTP {status}): {detail}")
    raise FalAPIError(f"fal request failed (HTTP {status}): {detail}", status_code=status)


def _as_png(data: bytes) -> tuple[bytes, tuple[int, int]]:
    """``data`` as PNG plus its (width, height); the service saves every image as .png."""
    image = Image.open(BytesIO(data))
    if image.format == "PNG":
        return data, image.size
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), image.size
