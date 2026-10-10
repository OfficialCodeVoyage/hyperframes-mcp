"""The picture model: Nano Banana 2 on fal, its input, its price, and what a failed picture says about being billed."""

from __future__ import annotations

import asyncio
import io
import json

import httpx
import pytest
from circuitbreaker import CircuitBreaker, CircuitBreakerError
from PIL import Image

from hyperframes_mcp.motion import generate, spend
from hyperframes_mcp.providers.image import fal_client
from hyperframes_mcp.providers.image.fal_client import (
    FalAPIError,
    FalImageClient,
    FalSubmitUncertainError,
    nano_banana_aspect_ratio,
    nano_banana_resolution,
)
from hyperframes_mcp.providers.image.settings import load_image_settings

QUEUE = "https://queue.fal.run/fal-ai/nano-banana-2"
STATUS = "https://queue.fal.run/fal-ai/nano-banana-2/requests/r1/status"
RESULT = "https://queue.fal.run/fal-ai/nano-banana-2/requests/r1"
CDN = "https://v3.fal.media/files/out.png"


def png(width: int = 64, height: int = 36) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, "PNG")
    return buffer.getvalue()


def fal(handler, *, quality_preset: str = "medium") -> FalImageClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FalImageClient(
        api_key="fal-test-not-a-key",  # scan:ignore
        settings=generate.picture_settings(),
        http_client=http,
        poll_interval=0,
        retry_wait=0,
    )


def job(sent: list[httpx.Request]):
    """A fal that takes the job, finishes it at once and serves one picture."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        url = str(request.url)
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "r1", "status_url": STATUS, "response_url": RESULT})
        if url == STATUS:
            return httpx.Response(200, json={"status": "COMPLETED"})
        if url == RESULT:
            return httpx.Response(200, json={"images": [{"url": CDN}], "description": ""})
        if url == CDN:
            return httpx.Response(200, content=png())
        return httpx.Response(404)

    return handler


@pytest.mark.parametrize(
    ("size", "ratio", "tier"),
    [
        ((1376, 768), "16:9", "1K"),
        ((2752, 1536), "16:9", "2K"),
        ((5504, 3072), "16:9", "4K"),
        ((768, 1376), "9:16", "1K"),
        ((1536, 2752), "9:16", "2K"),
        ((1024, 1024), "1:1", "1K"),
        ((1920, 1080), "16:9", "2K"),
    ],
)
def test_a_size_maps_to_the_nearest_accepted_ratio_and_its_tier(size, ratio, tier):
    assert nano_banana_aspect_ratio(*size) == ratio
    assert nano_banana_resolution(*size) == tier


def test_the_input_is_what_the_model_page_lists_and_has_no_image_size():
    body = fal_client.build_fal_input("fal-ai/nano-banana-2", "a vault", 2752, 1536, "high")

    assert body == {"prompt": "a vault", "aspect_ratio": "16:9", "resolution": "2K", "output_format": "png"}


def test_the_shipped_settings_draw_with_nano_banana_and_price_every_preset():
    settings = load_image_settings()

    assert (settings.provider, settings.model) == ("fal", "fal-ai/nano-banana-2")
    assert {name: settings.unit_cost(name) for name in settings.presets} == {"draft": 0.08, "production": 0.08}


def test_a_picture_costs_the_tier_of_its_quality():
    assert generate.picture_settings().provider == "fal"
    assert generate.picture_price("medium") == 0.08
    assert generate.picture_price("high") == 0.12


def test_a_job_is_submitted_with_the_ratio_and_tier_and_comes_back_as_a_priced_picture():
    sent: list[httpx.Request] = []

    result = asyncio.run(fal(job(sent)).generate("a vault", "high"))

    submit = sent[0]
    assert str(submit.url) == QUEUE and submit.headers["authorization"] == "Key fal-test-not-a-key"
    assert json.loads(submit.content) == {
        "prompt": "a vault",
        "aspect_ratio": "16:9",
        "resolution": "2K",
        "output_format": "png",
    }
    assert result.cost_usd == 0.12 and result.model == "fal-ai/nano-banana-2" and result.provider == "fal"
    assert (result.width, result.height) == (64, 36)
    assert all("authorization" not in request.headers for request in sent if str(request.url) == CDN)


def test_make_picture_draws_through_the_configured_provider(tmp_path):
    sent: list[httpx.Request] = []
    out = tmp_path / "vault.jpg"

    made = asyncio.run(generate.make_picture("a vault", out, quality="medium", client=fal(job(sent))))

    assert made["model"] == "fal-ai/nano-banana-2" and made["cost_usd"] == 0.08
    assert Image.open(out).size == (1920, 1080)
    assert json.loads(sent[0].content)["resolution"] == "1K"


def failing(status: int, *, on: str) -> object:
    """A fal that fails with HTTP ``status`` on the ``on`` request ("submit" or "status")."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            if on == "submit":
                return httpx.Response(status, text="no")
            return httpx.Response(200, json={"request_id": "r1", "status_url": STATUS, "response_url": RESULT})
        if url == STATUS:
            return httpx.Response(status, text="no")
        return httpx.Response(404)

    return handler


def failure_of(handler) -> BaseException:
    with pytest.raises(Exception) as caught:
        asyncio.run(fal(handler).generate("a vault", "medium"))
    return caught.value


def test_a_refusal_at_submit_is_known_not_to_be_billed():
    error = failure_of(failing(422, on="submit"))

    assert isinstance(error, FalAPIError) and spend._refused_before_sending(error)


def test_a_rate_limit_that_never_got_a_job_is_known_not_to_be_billed():
    error = failure_of(failing(429, on="submit"))

    assert getattr(error, "may_be_billed", None) is False and spend._refused_before_sending(error)


def test_a_failure_of_the_status_read_after_fal_took_the_job_may_be_billed_even_as_a_4xx():
    error = failure_of(failing(404, on="status"))

    assert getattr(error, "may_be_billed", None) is True and not spend._refused_before_sending(error)


def test_a_server_error_at_submit_is_not_repeated_and_may_be_billed():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(503, text="busy")

    error = failure_of(handler)

    assert calls == ["POST"] and isinstance(error, FalSubmitUncertainError)
    assert not spend._refused_before_sending(error)


def test_a_submit_answered_with_something_that_is_not_a_job_may_be_billed():
    error = failure_of(lambda request: httpx.Response(200, json={"oops": True}))

    assert isinstance(error, FalSubmitUncertainError) and not spend._refused_before_sending(error)


def test_a_result_without_a_picture_may_be_billed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"request_id": "r1", "status_url": STATUS, "response_url": RESULT})
        if str(request.url) == STATUS:
            return httpx.Response(200, json={"status": "COMPLETED"})
        return httpx.Response(200, json={"images": [], "description": "blocked"})

    error = failure_of(handler)

    assert isinstance(error, FalAPIError) and getattr(error, "may_be_billed", None) is True


def test_a_breaker_that_is_open_refused_the_job_before_anything_was_sent(tmp_path):
    class Open:
        provider_name, model = "fal", "fal-ai/nano-banana-2"

        async def generate(self, prompt, preset="draft"):
            raise CircuitBreakerError(CircuitBreaker(name="fal_image"))

    with pytest.raises(CircuitBreakerError) as caught:
        asyncio.run(generate.make_picture("x", tmp_path / "a.jpg", client=Open()))

    assert caught.value.may_be_billed is False and spend._refused_before_sending(caught.value)
