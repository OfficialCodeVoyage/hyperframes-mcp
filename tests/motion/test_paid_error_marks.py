"""What each paid call knows about whether it can have been billed, and the retry rule of the picture client: the spend
guard releases a hold only when that is known."""

from __future__ import annotations

import asyncio

import httpx
import openai
import pytest

from hyperframes_mcp.motion import generate
from hyperframes_mcp.providers.image.client import AIImageClient
from hyperframes_mcp.providers.image.fal_client import FalAPIError
from hyperframes_mcp.providers.video.adapter import I2VAdapter
from hyperframes_mcp.providers.video.budget import ClipBudget
from hyperframes_mcp.providers.video.errors import ClipGenerationError, ContentPolicyError, MissingKeyError
from hyperframes_mcp.providers.video.fal_video import FalJobError
from hyperframes_mcp.providers.video.settings import load_i2v_settings


class FakeFal:
    """A fal client that accepts the job (or not) and then fails with what it is told to."""

    def __init__(self, error: Exception, *, accepts: bool) -> None:
        self.error, self.accepts = error, accepts

    async def run(self, endpoint, payload, on_accepted=None):
        if self.accepts and on_accepted:
            on_accepted("job-1")
        raise self.error


def adapter(tmp_path, fal) -> I2VAdapter:
    settings = load_i2v_settings()

    async def url(_still):
        return "https://example.org/still.jpg"

    return I2VAdapter(
        fal, ClipBudget(settings.limits, tmp_path / "clips.db"), settings=settings, visuals_root=tmp_path, image_url_for=url
    )


async def run(tmp_path, error, *, accepts):
    still = tmp_path / "still.jpg"
    still.write_bytes(b"jpg")
    model = load_i2v_settings().model
    seconds = generate.allowed_clip_seconds(model)[0]
    with pytest.raises(Exception) as caught:
        await adapter(tmp_path, FakeFal(error, accepts=accepts)).generate_clip(
            still, "push in", model, seconds, topic_id=1, clip_id="c"
        )
    return caught.value


@pytest.mark.asyncio
async def test_a_policy_failure_after_fal_accepted_the_job_is_marked_as_possibly_billed(tmp_path):
    error = await run(tmp_path, FalJobError("blocked", "content_policy_violation"), accepts=True)

    assert isinstance(error, ContentPolicyError) and error.may_be_billed is True


@pytest.mark.asyncio
async def test_a_policy_refusal_at_submit_is_marked_as_not_billed(tmp_path):
    error = await run(tmp_path, FalAPIError("content_policy_violation", 422), accepts=False)

    assert isinstance(error, ContentPolicyError) and error.may_be_billed is False


@pytest.mark.asyncio
async def test_any_other_failure_after_the_job_was_accepted_is_marked_as_possibly_billed(tmp_path):
    error = await run(tmp_path, FalJobError("failed", "internal_server_error"), accepts=True)

    assert isinstance(error, ClipGenerationError) and error.may_be_billed is True


def test_a_missing_fal_key_is_its_own_error_and_nothing_was_sent():
    from hyperframes_mcp.providers.video.adapter import build_fal_client

    with pytest.raises(MissingKeyError, match="FAL_KEY"):
        build_fal_client(env={})


def test_a_cancelled_clip_is_marked_as_possibly_billed(tmp_path):
    still = tmp_path / "still.jpg"
    still.write_bytes(b"jpg")
    model = load_i2v_settings().model
    seconds = generate.allowed_clip_seconds(model)[0]

    async def go():
        await adapter(tmp_path, FakeFal(asyncio.CancelledError(), accepts=False)).generate_clip(
            still, "push in", model, seconds, topic_id=1, clip_id="c"
        )

    with pytest.raises(asyncio.CancelledError) as caught:
        asyncio.run(go())
    assert caught.value.may_be_billed is True


# -- the picture client never sends a paid request again after a dropped connection ---------------------------------


class Calls:
    def __init__(self, errors):
        self.errors, self.count = list(errors), 0

    async def generate(self, **kwargs):
        self.count += 1
        raise self.errors.pop(0)


def client_with(errors) -> tuple[AIImageClient, Calls]:
    client = AIImageClient(api_key="sk-test-not-a-key")  # scan:ignore
    calls = Calls(errors)
    client._client.images.generate = calls.generate
    return client, calls


def connection_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"))


@pytest.mark.asyncio
async def test_a_dropped_connection_is_sent_once_not_retried():
    client, calls = client_with([connection_error(), connection_error(), connection_error()])

    with pytest.raises(openai.APIConnectionError):
        await client.generate("a vault", "draft")

    assert calls.count == 1


def test_the_sdk_does_not_retry_on_its_own():
    client, _ = client_with([])

    assert client._client.max_retries == 0


# -- the second review ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failure_of_unknown_kind_before_the_job_was_accepted_is_marked_as_possibly_billed(tmp_path):
    error = await run(tmp_path, ValueError("not json"), accepts=False)

    assert error.may_be_billed is True  # only a refusal fal gave with a 4xx status at submit is known not to bill


@pytest.mark.asyncio
async def test_a_submit_answered_with_something_that_is_not_a_job_is_not_sent_again_and_may_be_billed():
    from hyperframes_mcp.providers.image.fal_client import FalSubmitUncertainError
    from hyperframes_mcp.providers.video.fal_video import FalVideoClient

    posts = []

    def answer(request: httpx.Request) -> httpx.Response:
        posts.append(request.url.path)
        return httpx.Response(200, text="<html>a proxy page</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        with pytest.raises(FalSubmitUncertainError):
            await FalVideoClient("key", http_client=http).run("fal-ai/some/endpoint", {})  # scan:ignore

    assert len(posts) == 1


# -- a paid file is written where nothing is, never through a link ----------------------------------------------


def test_a_link_put_at_the_name_during_the_call_is_not_written_through(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    out = tmp_path / "assets" / "later.png"
    out.parent.mkdir()
    out.symlink_to(outside)

    with pytest.raises(generate.GenerateError, match=r"kept as later\.paid-1\.png"):
        generate.write_new(out, b"the paid picture")

    assert outside.read_text() == "keep me"
    assert (tmp_path / "assets" / "later.paid-1.png").read_bytes() == b"the paid picture"


def test_a_dangling_link_and_a_file_made_meanwhile_are_not_overwritten_either(tmp_path):
    dangling = tmp_path / "a.png"
    dangling.symlink_to(tmp_path / "nowhere.png")
    taken = tmp_path / "b.png"
    taken.write_bytes(b"theirs")

    for target in (dangling, taken):
        with pytest.raises(generate.GenerateError, match="appeared"):
            generate.write_new(target, b"mine")

    assert not (tmp_path / "nowhere.png").exists() and taken.read_bytes() == b"theirs"


def test_a_new_name_is_just_written(tmp_path):
    target = tmp_path / "new.mp4"
    source = tmp_path / "scratch.mp4"
    source.write_bytes(b"clip")

    assert generate.write_new(target, source) == target and target.read_bytes() == b"clip"


@pytest.mark.asyncio
async def test_make_picture_whose_name_was_taken_meanwhile_keeps_the_paid_picture_and_says_it_may_be_billed(tmp_path):
    from tests.motion.test_generate import FakeImages, made_by

    out = tmp_path / "edits" / "vault.jpg"

    class Racing(FakeImages):
        async def generate(self, prompt, preset):
            out.parent.mkdir(parents=True)
            out.symlink_to(tmp_path / "elsewhere.jpg")  # the agent links the name while the provider works
            return await super().generate(prompt, preset)

    with pytest.raises(generate.GenerateError) as caught:
        await generate.make_picture("a vault", out, client=Racing(made_by()))

    assert caught.value.may_be_billed is True
    assert not (tmp_path / "elsewhere.jpg").exists()
    assert (tmp_path / "edits" / "vault.paid-1.jpg").is_file()


@pytest.mark.asyncio
async def test_a_request_fal_cannot_have_taken_is_marked_as_not_billed(tmp_path):
    from hyperframes_mcp.providers.image.fal_client import FalNotSentError

    error = await run(tmp_path, FalNotSentError("fal rate limit (HTTP 429)"), accepts=False)

    assert error.may_be_billed is False
