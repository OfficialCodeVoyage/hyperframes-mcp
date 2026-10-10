"""FalVideoClient: one job on fal.ai's queue for an I2V endpoint.

A thin subclass of G1's ``FalImageClient``. It keeps that client's auth header, its retry policy
(status and result reads on a dropped connection, 429 and 5xx; the submit POST only on 429 or a
connection that was never made) and its ``status_url`` / ``response_url`` following, so the API key
is handled in exactly one place. What it adds:

* ``run``: submit, report the moment fal accepts the job (the adapter needs to know whether a
  failed attempt may have been billed), poll, and return the output JSON. The image job and its
  circuit breaker are not used.
* ``_wait_until_complete`` is overridden to keep fal's machine-readable ``error_type`` (the
  parent drops it), which is how a content-policy rejection is told from other failures.

Facts, read 2026-10-01 (https://fal.ai/docs/model-apis/model-endpoints/queue and
https://fal.ai/docs/model-apis/errors): the queue API is ``POST https://queue.fal.run/{endpoint}``
with ``Authorization: Key $FAL_KEY``; the status response carries ``error`` and ``error_type``
for a failed job; ``content_policy_violation`` is a non-retryable HTTP 422 and the other
model-level errors listed there are 422 too. The docs do not say whether a failed request is
billed, so the adapter assumes it may be once fal accepted the job.

A submit whose response was lost, or that came back 5xx, may already have created a job. fal's queue
documents no idempotency key, so it is not retried: the parent raises ``FalSubmitUncertainError`` after
one POST and the adapter charges that one possible job at its estimate.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import httpx

from hyperframes_mcp.providers.image.fal_client import (
    QUEUE_BASE,
    FalAPIError,
    FalImageClient,
    FalSubmitUncertainError,
    FalTimeoutError,
)
from hyperframes_mcp.providers.image.pricing import PricingTable
from hyperframes_mcp.providers.image.settings import ImagePreset, ImageSettings
from hyperframes_mcp.providers.video.errors import ClipGenerationError

_PENDING_STATES = ("IN_QUEUE", "IN_PROGRESS")
_PLACEHOLDER_MODEL = "fal-ai/i2v-queue"


class FalJobError(FalAPIError):
    """fal accepted the job and then reported it failed; ``error_type`` is fal's code."""

    def __init__(self, message: str, error_type: str | None = None) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class FalJobResult:
    request_id: str
    output: dict[str, Any]


def _placeholder_settings() -> ImageSettings:
    """What ``FalImageClient`` insists on at construction; the video path never reads it."""
    return ImageSettings(
        provider="fal",
        model=_PLACEHOLDER_MODEL,
        presets={"draft": ImagePreset(quality="low", size="1024x1024")},
        pricing=PricingTable.model_validate({"fal": {_PLACEHOLDER_MODEL: {"flat": 0.0}}}),
    )


class FalVideoClient(FalImageClient):
    """Runs I2V jobs on fal's queue. ``api_key`` may be None; ``run`` then refuses."""

    def __init__(
        self,
        api_key: str | None,
        *,
        http_client: httpx.AsyncClient | None = None,
        poll_interval: float = 2.0,
        timeout: float = 600.0,
        retry_wait: float = 2.0,
    ) -> None:
        super().__init__(
            api_key,
            _placeholder_settings(),
            max_concurrent=1,
            http_client=http_client,
            poll_interval=poll_interval,
            timeout=timeout,
            retry_wait=retry_wait,
        )

    async def run(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        on_accepted: Callable[[str], None] | None = None,
    ) -> FalJobResult:
        """Submit ``body`` to ``endpoint`` and wait for its output.

        Args:
            endpoint: A fal endpoint id such as ``fal-ai/veo3.1/lite/image-to-video``.
            body: The model input JSON.
            on_accepted: Called with the request id as soon as fal accepts the job.

        Raises:
            ClipGenerationError: no API key.
            FalJobError: fal reported the job failed (carries ``error_type``).
            FalSubmitUncertainError: the submit may have created a job (a 5xx or a lost answer);
                it was sent once and is not repeated.
            FalAPIError: an HTTP 4xx (for example a 422 content-policy rejection at submit).
            FalTimeoutError: the job was still queued or running after the timeout.
        """
        if not self._api_key:
            raise ClipGenerationError("No fal API key is configured")
        async with (
            nullcontext(self._http_client)
            if self._http_client is not None
            else httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True)
        ) as http:
            response = await self._request(http, "POST", f"{QUEUE_BASE}/{endpoint}", json=body)
            try:
                submit = response.json()
                request_id, _, _ = submit["request_id"], submit["status_url"], submit["response_url"]
            except (ValueError, KeyError, TypeError) as error:
                # fal answered the POST, so a job may exist whose id was never seen: it is not sent again, and the
                # caller charges one possible job
                raise FalSubmitUncertainError("fal answered the submit with something that is not a job") from error
            if on_accepted is not None:
                on_accepted(request_id)
            await self._wait_until_complete(http, submit["status_url"])
            output = (await self._request(http, "GET", submit["response_url"])).json()
        return FalJobResult(request_id, output)

    async def _wait_until_complete(self, http: httpx.AsyncClient, status_url: str) -> None:
        deadline = time.monotonic() + self._timeout
        while True:
            status = (await self._request(http, "GET", status_url)).json()
            if status.get("error") or status.get("error_type"):
                raise FalJobError(
                    f"fal job failed: {status.get('error') or status.get('error_type')}",
                    status.get("error_type"),
                )
            state = status.get("status")
            if state == "COMPLETED":
                return
            if state not in _PENDING_STATES:
                raise FalAPIError(f"Unexpected fal job status {state!r}")
            if time.monotonic() >= deadline:
                raise FalTimeoutError(f"fal job still {state} after {self._timeout:.0f}s")
            await asyncio.sleep(self._poll_interval)
