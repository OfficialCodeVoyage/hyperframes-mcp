"""Paid generation for an edit, with the providers faked: the keys, a picture and a clip, each with its record."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
from datetime import UTC, datetime
from fractions import Fraction

import pytest
from PIL import Image

from hyperframes_mcp.motion import generate, keys, sheet, spend
from hyperframes_mcp.motion.manifest import Canvas
from hyperframes_mcp.motion.project_edit import AI_CREDIT
from hyperframes_mcp.providers.image.models import AIImageResult
from hyperframes_mcp.providers.video.adapter import ClipResult
from hyperframes_mcp.providers.video.errors import ClipLengthError
from hyperframes_mcp.qa.media import first_stream, probe_media, resolve_tools

KEYS = ("OPENAI_API_KEY", "FAL_KEY")


@pytest.fixture
def environment(monkeypatch) -> dict[str, str]:
    """A process environment without the keys, thrown away after the test, where ``.env`` files load again (the test's own home is the only
    ``.env`` it reads.
    """
    env = {k: v for k, v in os.environ.items() if k not in (*KEYS, "PYTHON_DOTENV_DISABLED")}
    monkeypatch.setattr(os, "environ", env)
    return env


def test_a_key_comes_from_the_environment_then_the_env_file_of_the_home_and_then_the_keychain(
    profiles_root, environment, monkeypatch
):
    env_file = profiles_root.home / "config" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OPENAI_API_KEY=from-file\nFAL_KEY=fal-file\n")
    environment["FAL_KEY"] = "from-environment"
    monkeypatch.setattr(keys, "_from_keychain", lambda name: f"keychain-{name}")

    keys.load_keys()

    assert environment["OPENAI_API_KEY"] == "from-file"
    assert environment["FAL_KEY"] == "from-environment"


def test_a_key_that_is_in_neither_the_environment_nor_the_file_is_read_from_the_keychain(
    profiles_root, environment, monkeypatch
):
    monkeypatch.setattr(keys, "_from_keychain", lambda name: f"keychain-{name}")

    keys.load_keys()

    assert environment["OPENAI_API_KEY"] == "keychain-OPENAI_API_KEY"
    assert environment["FAL_KEY"] == "keychain-FAL_KEY"


def make_media(path, *, size: str = "1280x720", fps: int = 24, seconds: float = 0.5, sound=False):
    """A small real clip made with ffmpeg (what the clip model returns: 1280x720 at 24 fps, no sound)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sheet.ffmpeg_binary(), "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=s={size}:r={fps}:d={seconds}",
    ]  # fmt: skip
    if sound:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run([*command, "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def stream_facts(path) -> tuple[int, int, Fraction, bool]:
    """The width, height and frame rate of the video stream, and whether the file has sound."""
    probe = probe_media(path, resolve_tools(sheet.media_environment()))
    video = first_stream(probe, "video")
    return (
        video["width"],
        video["height"],
        Fraction(video["r_frame_rate"]),
        first_stream(probe, "audio") is not None,
    )


def picture_bytes(size=(2048, 1152), colour="#c8b89a") -> bytes:
    """A real PNG, as the image client hands back (it has decoded the picture before it returns it)."""
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, "PNG")
    return buffer.getvalue()


def made_by(size=(2048, 1152)) -> AIImageResult:
    """What the image client returns for a picture of ``size``."""
    return AIImageResult(
        image_bytes=picture_bytes(size),
        width=size[0],
        height=size[1],
        quality="high",
        cost_usd=0.055,
        model=generate.PICTURE_MODEL,
    )


class FakeImages:
    def __init__(self, result: AIImageResult | None) -> None:
        self.result, self.calls = result, []

    async def generate(self, prompt: str, preset: str) -> AIImageResult | None:
        self.calls.append((prompt, preset))
        return self.result


def test_a_picture_is_drawn_in_the_profile_style_fitted_to_the_canvas_as_a_light_jpeg_and_written_with_its_record(
    tmp_path,
):
    images = FakeImages(made_by())
    out = tmp_path / "edits" / "vault.jpg"

    record = asyncio.run(generate.make_picture("a vault door by lamplight", out, client=images))

    prompt, preset = images.calls[0]
    assert "a vault door by lamplight" in prompt and "Full-bleed" in prompt
    assert preset == "high"
    written = Image.open(out)
    assert (written.format, written.size) == ("JPEG", (1920, 1080))
    kept = json.loads((tmp_path / "edits" / "vault.jpg.json").read_text())
    assert kept == record
    assert (record["ai"], record["credit"], record["cost_usd"]) == (
        True,
        AI_CREDIT,
        0.055,
    )
    assert record["request"] == "a vault door by lamplight"
    # the record keeps the whole prompt that was sent, so the picture can be remade and audited
    assert record["prompt"] == prompt
    assert record["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert (record["size"], record["generated_size"]) == ("1920x1080", "2048x1152")


def test_a_picture_is_fitted_to_the_canvas_the_project_has_and_a_png_name_is_kept_as_a_png(
    tmp_path,
):
    # a square picture: red above, blue below; it is scaled to cover the canvas and cropped from the middle
    square = Image.new("RGB", (1000, 1000), "#d02020")
    square.paste(Image.new("RGB", (1000, 500), "#2020d0"), (0, 500))
    buffer = io.BytesIO()
    square.save(buffer, "PNG")
    result = AIImageResult(
        image_bytes=buffer.getvalue(),
        width=1000,
        height=1000,
        quality="high",
        cost_usd=0.055,
    )
    out = tmp_path / "square.png"

    record = asyncio.run(
        generate.make_picture(
            "a vault",
            out,
            client=FakeImages(result),
            canvas=Canvas(width=1280, height=720, fps=24),
        )
    )

    written = Image.open(out)
    assert (written.format, written.size) == ("PNG", (1280, 720))
    assert (record["size"], record["generated_size"]) == ("1280x720", "1000x1000")
    top, bottom = written.getpixel((640, 100)), written.getpixel((640, 620))
    assert top[0] > top[2] and bottom[2] > bottom[0]  # red above, blue below


def test_a_picture_that_already_has_the_canvas_size_is_not_scaled(tmp_path):
    out = tmp_path / "exact.jpg"
    result = made_by((320, 180))

    asyncio.run(
        generate.make_picture(
            "x",
            out,
            client=FakeImages(result),
            canvas=Canvas(width=320, height=180, fps=24),
        )
    )

    assert Image.open(out).size == (320, 180)


def test_a_picture_file_that_is_not_a_jpeg_or_png_name_is_refused_before_anything_is_sent(
    tmp_path,
):
    images = FakeImages(made_by())

    with pytest.raises(generate.GenerateError, match=r"\.jpg or \.png"):
        asyncio.run(generate.make_picture("x", tmp_path / "x.gif", client=images))
    assert images.calls == [] and not list(tmp_path.iterdir())


def test_a_preview_is_a_small_jpeg_640_wide_and_a_narrow_picture_is_not_enlarged(
    tmp_path,
):
    wide = tmp_path / "wide.jpg"
    Image.new("RGB", (1920, 1080), "#c8b89a").save(wide)
    narrow = tmp_path / "narrow.png"
    Image.new("RGB", (320, 180), "#c8b89a").save(narrow)

    wide_preview = Image.open(io.BytesIO(generate.picture_preview(wide)))
    narrow_preview = Image.open(io.BytesIO(generate.picture_preview(narrow)))

    assert (wide_preview.format, wide_preview.size) == ("JPEG", (640, 360))
    assert narrow_preview.size == (320, 180)
    assert len(generate.picture_preview(wide)) < 100_000


def test_a_picture_is_drawn_in_the_style_of_the_profile_it_is_for(tmp_path, profiles_root):
    profiles_root.write("history", "image_style.yaml", "prefix: 'Woodcut of a counting house. Scene: '\n")
    result = made_by()
    images = FakeImages(result)

    record = asyncio.run(generate.make_picture("a vault", tmp_path / "v.png", profile_name="history", client=images))

    sent = images.calls[0][0]
    assert sent.startswith("Woodcut of a counting house. Scene: a vault")
    assert sent == generate.picture_prompt("a vault", "history")
    assert record["profile"] == "history"
    other = FakeImages(result)
    asyncio.run(generate.make_picture("a vault", tmp_path / "w.png", client=other))
    assert not other.calls[0][0].startswith("Woodcut")
    assert other.calls[0][0] == generate.picture_prompt("a vault")


def test_a_picture_without_a_key_says_which_key_and_writes_nothing(tmp_path, monkeypatch):
    out = tmp_path / "vault.png"

    with pytest.raises(generate.GenerateError, match="FAL_KEY"):
        asyncio.run(generate.make_picture("x", out, client=FakeImages(None)))
    monkeypatch.delenv("FAL_KEY", raising=False)
    with pytest.raises(generate.GenerateError, match="FAL_KEY"):  # no client given: the provider's own, no key
        asyncio.run(generate.make_picture("x", out))
    assert not out.exists()


def fake_adapter(seen, clip_path):
    """An adapter whose job is the 1280x720 clip ``clip_path`` (the clip model's size), and that notes what it was asked."""

    class FakeAdapter:
        async def generate_clip(self, sent, motion, model, seconds, *, topic_id, clip_id):
            seen["size"] = Image.open(sent).size
            seen["cap_id"] = topic_id
            path = sent.with_name("downloaded.mp4")
            path.write_bytes(clip_path.read_bytes())
            return ClipResult(path, clip_id, 1, model, seconds, 0.12, 30.0, "req-1")

    return lambda settings, log, scratch: seen.update(log=log) or FakeAdapter()


def test_a_clip_is_made_from_a_full_frame_still_under_the_projects_cap_and_fitted_to_the_canvas(tmp_path, monkeypatch):
    still = tmp_path / "vault.png"
    Image.new("RGB", (2048, 1152), "black").save(still)
    seen = {}
    monkeypatch.setattr(generate, "_adapter", fake_adapter(seen, make_media(tmp_path / "model.mp4")))
    project, out = tmp_path / "project", tmp_path / "project" / "edits" / "door.mp4"

    record = asyncio.run(generate.make_clip(still, "slow push-in", out, project=project, seconds=4, topic_id=7))

    assert stream_facts(out) == (1920, 1080, 24, False)
    assert seen == {
        "log": project / generate.CLIP_LOG,
        "size": (1920, 1080),
        "cap_id": generate.CLIP_CAP_ID,
    }
    assert (record["made_by"], record["still"], record["cost_usd"]) == (
        "animate",
        "vault.png",
        0.12,
    )
    assert (record["size"], record["topic_id"]) == ("1920x1080", 7)
    assert json.loads(out.with_name("door.mp4.json").read_text()) == record
    assert not list(out.parent.glob(".*.fit.mp4"))


def test_a_clip_is_fitted_to_the_canvas_the_project_has(tmp_path, monkeypatch):
    still = tmp_path / "vault.png"
    Image.new("RGB", (64, 64), "black").save(still)
    monkeypatch.setattr(generate, "_adapter", fake_adapter({}, make_media(tmp_path / "model.mp4")))
    out = tmp_path / "project" / "door.mp4"

    record = asyncio.run(
        generate.make_clip(
            still,
            "x",
            out,
            project=tmp_path / "project",
            seconds=4,
            canvas=Canvas(width=1280, height=720, fps=24),
        )
    )

    assert stream_facts(out)[:2] == (1280, 720)
    assert record["size"] == "1280x720"


def test_a_clip_that_cannot_be_fitted_is_kept_as_it_came_with_a_warning(tmp_path, monkeypatch):
    still = tmp_path / "vault.png"
    Image.new("RGB", (64, 64), "black").save(still)
    not_a_video = tmp_path / "model.mp4"
    not_a_video.write_bytes(b"mp4")
    monkeypatch.setattr(generate, "_adapter", fake_adapter({}, not_a_video))
    out = tmp_path / "project" / "door.mp4"

    record = asyncio.run(generate.make_clip(still, "x", out, project=tmp_path / "project", seconds=4))

    assert out.read_bytes() == b"mp4"
    assert "cannot fit door.mp4 to the canvas" in record["warning"]
    assert "size" not in record
    assert record["cost_usd"] == 0.12


def test_a_clip_length_the_model_cannot_make_is_refused_with_the_lengths_it_can(tmp_path, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("a call was prepared")

    monkeypatch.setattr(generate, "_adapter", never)

    assert generate.allowed_clip_seconds("veo-3.1-lite") == (4, 6, 8)
    assert generate.allowed_clip_seconds("minimax-h3") == (5,)
    with pytest.raises(ClipLengthError, match=r"makes 4, 6, 8 s \(the nearest is 6 s\)"):
        generate.clip_price(5)
    with pytest.raises(ClipLengthError, match="the nearest is 8 s"):
        generate.check_clip_seconds(12, "veo-3.1-lite")
    with pytest.raises(ClipLengthError, match="makes 4, 6, 8 s"):
        asyncio.run(generate.make_clip(tmp_path / "v.png", "x", tmp_path / "v.mp4", project=tmp_path, seconds=5))
    assert generate.clip_price(6) == pytest.approx(0.18)


def test_a_clip_is_scaled_up_and_cropped_to_the_canvas_with_its_frame_rate_and_no_sound(
    tmp_path,
):
    canvas = Canvas(width=1920, height=1080, fps=24)
    wide = make_media(tmp_path / "wide.mp4", size="1280x720", fps=24, sound=True)
    square = make_media(tmp_path / "square.mp4", size="800x800", fps=30)

    assert generate.fit_to_canvas(wide, canvas) == (1920, 1080)
    assert generate.fit_to_canvas(square, canvas) == (1920, 1080)

    assert stream_facts(wide) == (1920, 1080, 24, False)  # its sound is not kept
    assert stream_facts(square) == (1920, 1080, 30, False)  # its own frame rate is
    assert not list(tmp_path.glob(".*.fit.mp4"))


def test_a_clip_that_has_the_canvas_size_is_left_as_it_is(tmp_path):
    clip = make_media(tmp_path / "exact.mp4", size="320x180")
    before = clip.read_bytes()

    size = generate.fit_to_canvas(clip, Canvas(width=320, height=180, fps=24))

    assert size == (320, 180)
    assert clip.read_bytes() == before


def test_a_file_that_is_not_a_clip_stays_as_it_was_when_it_cannot_be_fitted(tmp_path):
    clip = tmp_path / "broken.mp4"
    clip.write_bytes(b"not a video")

    with pytest.raises(generate.GenerateError, match="cannot fit broken.mp4"):
        generate.fit_to_canvas(clip)
    assert clip.read_bytes() == b"not a video"


@pytest.fixture
def confirms(monkeypatch):
    """A stand-in for ``spend.confirm`` that notes what it was asked and answers from the quote the command saved, so
    a command is tested without a database or a paid call."""
    calls = []

    async def confirm(project, quote_id, *, now=None, session_factory=None):
        calls.append((project, quote_id))
        book = json.loads((project / ".video-mcp" / "quotes.json").read_text())
        quote = book["quotes"][quote_id]
        spend._append_spend(
            project,
            {"at": datetime.now(UTC).isoformat(), "usd": quote["price_usd"]},
        )
        return {
            "quote_id": quote_id,
            "kind": quote["kind"],
            "path": quote["arguments"]["out"],
            "cost_usd": quote["price_usd"],
            "credit": AI_CREDIT,
            "recorded": {"where": "cost_ledger", "topic_id": 7},
            "spent_usd": quote["price_usd"],
            "left_usd": 10 - quote["price_usd"],
        }

    monkeypatch.setattr(spend, "confirm", confirm)
    return calls


@pytest.fixture
def no_paid_call(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("a paid call was made")

    for target, name in (
        (generate, "make_picture"),
        (generate, "make_clip"),
        (keys, "load_keys"),
        (spend, "confirm"),
    ):
        monkeypatch.setattr(target, name, never)
