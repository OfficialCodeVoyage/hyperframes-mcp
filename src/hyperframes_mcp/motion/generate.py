"""Paid generation for an edit in the desktop app: one picture in the profile's style, or one clip made from a picture.

The recipes: a picture drawn by the configured image provider (Nano Banana 2 on fal, at 1K or 2K) with the
profile's style guide and a full-bleed note, and the fal image-to-video adapter with its per-video cap (here per
project, its attempt log in ``<project>/.motion/``). A picture (a PNG of several MB) is fitted to the project's
canvas and kept as a JPEG of about 1 MB, or as a PNG when its file name says so; a clip comes back at 1280x720 and is
scaled to the canvas before it is kept. The caller loads the keys first (``keys.load_keys``). Every file gets a record
beside it (``<file>.json``: the AI credit, what made it, the request, for a picture the full prompt sent, and the price)
that ``project_edit.replace_media`` reads.
What may be spent, and the quote that comes before a call, is ``src/hyperframes_mcp/motion/spend.py``. Imported only by the paid
commands: it brings the provider clients with it.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import io
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from circuitbreaker import CircuitBreakerError
from PIL import Image, ImageOps

from hyperframes_mcp.motion import sheet
from hyperframes_mcp.motion.manifest import Canvas, default_canvas
from hyperframes_mcp.motion.profiles import DEFAULT_PROFILE, load_profile
from hyperframes_mcp.motion.project_edit import AI_CREDIT, PICTURE_QUALITIES
from hyperframes_mcp.providers.bounded_fetch import save_shrunk_jpeg
from hyperframes_mcp.providers.image.providers import ImageClient, create_image_client, get_provider
from hyperframes_mcp.providers.image.settings import (
    ImagePreset,
    ImageSettings,
    load_image_settings,
)
from hyperframes_mcp.providers.image.style import load_image_style, resolve_style_guide
from hyperframes_mcp.providers.video.adapter import I2VAdapter, build_fal_client
from hyperframes_mcp.providers.video.budget import ClipBudget
from hyperframes_mcp.providers.video.errors import ClipLengthError
from hyperframes_mcp.providers.video.pricing import (
    estimate_clip_cost,
    get_model,
    nearest_duration,
)
from hyperframes_mcp.providers.video.settings import I2VSettings, load_i2v_settings
from hyperframes_mcp.qa.media import first_stream, probe_media, resolve_tools, run_command
from hyperframes_mcp.qa.models import QAToolError, UnreadableMediaError
from hyperframes_mcp.util.atomic import atomic_write_bytes

PICTURE_PROVIDER = "fal"
PICTURE_MODEL = "fal-ai/nano-banana-2"  # Google's Nano Banana 2 on fal: one FAL_KEY for pictures and clips
# What each quality draws, 16:9 like the canvas: "medium" is the 1K tier (about 1376 px wide), "high" the 2K tier
# (2752 px wide, sharper than the 1920x1080 canvas it is fitted to). Their prices are in config/settings.yaml.
PICTURE_SIZES = {"medium": "1376x768", "high": "2752x1536"}
FULL_BLEED = (
    " Full-bleed composition that fills the whole frame to its edges: no border, no paper margin,"
    " no frame, no vignette frame."
)
# the format of a picture's file, from its suffix: a JPEG is the light one, a PNG is kept for a name that asks for it
PICTURE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}
PICTURE_JPEG_QUALITY = 90  # 4:2:0 at 1920x1080: 0.9 to 1.1 MB for the engraved stills (their PNGs are 6 to 7 MB)
PREVIEW_EDGE = 640  # the longest edge of a preview, a JPEG of about 65 KB
PREVIEW_JPEG_QUALITY = 70
STILL_EDGE = 1920  # the longest edge of the picture a clip is made from, sent inline (the adapter's limit is 7 MB)
CLIP_LOG = ".motion/i2v_clips.db"
# The clip cap counts rows by a video id. A project's log file holds one project, so its rows all share this id: giving
# a project a pipeline topic later (``adopt``) then does not start its clip count again.
CLIP_CAP_ID = 0


class GenerateError(RuntimeError):
    """The paid call was not made, or did not give a file."""


def picture_settings() -> ImageSettings:
    presets = {"draft": ImagePreset(quality="low", size=PICTURE_SIZES["medium"])} | {
        quality: ImagePreset(quality=quality, size=PICTURE_SIZES[quality]) for quality in PICTURE_QUALITIES
    }
    return ImageSettings(
        provider=PICTURE_PROVIDER,
        model=PICTURE_MODEL,
        presets=presets,
        pricing=load_image_settings().pricing,
    )


def picture_price(quality: str) -> float:
    """The estimated USD of one picture (the repository's list-price table)."""
    return picture_settings().unit_cost(quality)


def picture_prompt(request: str, profile_name: str = DEFAULT_PROFILE) -> str:
    """The full prompt a picture of ``request`` sends: the profile's style frame around the request, then the
    full-bleed note. Nothing is called; ``make_picture`` sends exactly this."""
    profile = load_profile(profile_name)
    guide = resolve_style_guide(profile.style_guide_path, profile.niche)
    return load_image_style(profile.config_dir).render(request + FULL_BLEED, guide)


def picture_format(out: Path) -> str:
    """The format of the picture file ``out`` (``JPEG`` or ``PNG``), from its suffix.

    Raises:
        GenerateError: the suffix is neither ``.jpg``, ``.jpeg`` nor ``.png``.
    """
    try:
        return PICTURE_FORMATS[out.suffix.lower()]
    except KeyError:
        raise GenerateError(f"{out.name}: a picture file must end in .jpg or .png") from None


def fit_picture(data: bytes, kind: str, canvas: Canvas | None = None) -> tuple[bytes, tuple[int, int]]:
    """The picture ``data`` at the project's canvas size, encoded as ``kind`` (``"JPEG"`` or ``"PNG"``); returns the
    bytes and the size.

    A picture of the canvas's shape (the model's 2048x1152 is 16:9) is scaled; one of another shape is scaled up
    (Lanczos) to cover the canvas and cropped from the middle. A JPEG is written at a high quality.
    """
    canvas = canvas or default_canvas()
    fitted = ImageOps.fit(
        Image.open(io.BytesIO(data)).convert("RGB"),
        (canvas.width, canvas.height),
        Image.LANCZOS,
    )
    options = {"quality": PICTURE_JPEG_QUALITY, "optimize": True} if kind == "JPEG" else {}
    buffer = io.BytesIO()
    fitted.save(buffer, kind, **options)
    return buffer.getvalue(), fitted.size


def _create_new(path: Path):
    """Open ``path`` for writing only if nothing is there, not even a link. The file of a paid call is written minutes
    after the name was checked, and the folder is the agent's to write to, so a link put there meanwhile must not make
    the write land somewhere else."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o644), "wb")


def write_new(path: Path, source: bytes | Path) -> Path:
    """Write ``source`` (bytes, or the file at that path) to ``path`` if nothing is there. If something appeared since
    the name was chosen, the paid result is kept beside it under a name that is free, and that is said.

    Raises:
        GenerateError: something is at ``path``; the message names the file the result was kept in.
    """
    for target in (path, *(path.with_name(f"{path.stem}.paid-{n}{path.suffix}") for n in range(1, 100))):
        try:
            handle = _create_new(target)
        except FileExistsError:
            continue
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.EISDIR):  # a link or a folder is there
                continue
            raise
        with handle:
            if isinstance(source, Path):
                with source.open("rb") as origin:
                    shutil.copyfileobj(origin, handle)
            else:
                handle.write(source)
        if target == path:
            return path
        raise GenerateError(
            f"{path.name} appeared while the paid file was being made, so it was not written over; "
            f"the result is kept as {target.name}"
        )
    raise GenerateError(f"{path.name} is taken, and so are the names beside it; the paid result could not be kept")


def picture_preview(path: Path) -> bytes:
    """A small JPEG (``PREVIEW_EDGE`` px on its longest edge, never enlarged) of the picture at ``path``, for a person
    or an agent to look at before the picture goes in a layer.

    Raises:
        OSError: ``path`` cannot be read as a picture.
    """
    with Image.open(path) as image:
        preview = image.convert("RGB")
    preview.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.LANCZOS)
    buffer = io.BytesIO()
    preview.save(buffer, "JPEG", quality=PREVIEW_JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


async def make_picture(
    request: str,
    out: Path,
    *,
    quality: str = "high",
    profile_name: str = DEFAULT_PROFILE,
    canvas: Canvas | None = None,
    client: ImageClient | None = None,
) -> dict:
    """Make one picture of ``request`` in the style of profile ``profile_name`` into ``out`` (a paid call), fitted to
    ``canvas`` (default: the motion tokens' 1920x1080) and written as a JPEG or PNG by its suffix, and write its record
    (with the full prompt sent and its sha256, so the picture can be remade and audited).

    Raises:
        GenerateError: ``out`` does not end in ``.jpg`` or ``.png``, or there is no key; nothing was sent.
    """
    picture_format(out)
    prompt = picture_prompt(request, profile_name)
    settings = picture_settings()
    client = client or create_image_client(settings)
    try:
        result = await client.generate(prompt, quality) if client else None
    except CircuitBreakerError as error:
        error.may_be_billed = False  # the breaker refused the job before anything was sent
        raise
    if result is None:
        key = get_provider(settings.provider).api_key_env
        raise GenerateError(f"{key} is not set (environment, ~/.hyperframes-mcp/config/.env or the Keychain)")
    try:  # the provider has answered: whatever fails from here on, the picture was paid for
        out.parent.mkdir(parents=True, exist_ok=True)
        data, (width, height) = await asyncio.to_thread(fit_picture, result.image_bytes, picture_format(out), canvas)
        write_new(out, data)
        return _record(
            out,
            made_by="picture",
            model=result.model,
            quality=result.quality,
            size=f"{width}x{height}",
            generated_size=f"{result.width}x{result.height}",
            request=request,
            prompt=prompt,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            profile=profile_name,
            cost_usd=result.cost_usd,
        )
    except Exception as error:
        error.may_be_billed = True
        raise


def allowed_clip_seconds(model: str | None = None) -> tuple[int, ...]:
    """The lengths in seconds ``model`` can make (default: the I2V settings' model), from the repository's price table.

    Raises:
        UnknownModelError: the model is not in the table.
    """
    return get_model(model or load_i2v_settings().model).durations


def check_clip_seconds(seconds: int, model: str | None = None) -> None:
    """Refuse a length ``model`` cannot make, before anything is sent or counted.

    Raises:
        ClipLengthError: the message names the lengths the model can make and the nearest one.
    """
    model = model or load_i2v_settings().model
    allowed = allowed_clip_seconds(model)
    if seconds in allowed:
        return
    try:
        nearest = nearest_duration(model, seconds)
    except ClipLengthError:
        nearest = max(allowed)
    raise ClipLengthError(
        f"{model} cannot make a {seconds} s clip: it makes {', '.join(str(s) for s in allowed)} s "
        f"(the nearest is {nearest} s)"
    )


def clip_price(seconds: int) -> float:
    """The estimated USD of one clip on the I2V settings' model (the repository's list-price table).

    Raises:
        ClipLengthError: the model cannot make a ``seconds`` clip (the message names the lengths it can).
    """
    model = load_i2v_settings().model
    check_clip_seconds(seconds, model)
    return estimate_clip_cost(model, seconds)


def fit_to_canvas(clip: Path, canvas: Canvas | None = None) -> tuple[int, int]:
    """Scale ``clip`` to the project's canvas, in place; returns its size afterwards.

    The clip model returns 1280x720 while the video is 1920x1080. A clip of another size is scaled up (Lanczos) to
    cover the canvas and cropped from the middle where the shape differs, and written again as H.264 at a high quality
    with its frame rate kept and no sound. A clip that already has the canvas's size is left as it is.

    Raises:
        GenerateError: ffmpeg or ffprobe is missing, or cannot read or write the clip; the clip is left as it was.
    """
    canvas = canvas or default_canvas()
    # a name nobody can guess, made by us: a fixed one beside the clip could be a link put there to be written through
    scratch_fd, scratch_name = tempfile.mkstemp(dir=clip.parent, prefix=f".{clip.stem}.fit.", suffix=".mp4")
    os.close(scratch_fd)
    fitted = Path(scratch_name)
    try:
        return _fit_to_canvas(clip, canvas, fitted)
    finally:
        fitted.unlink(missing_ok=True)


def _fit_to_canvas(clip: Path, canvas: Canvas, fitted: Path) -> tuple[int, int]:
    try:
        tools = resolve_tools(sheet.media_environment())
        stream = first_stream(probe_media(clip, tools), "video")
        if stream is None:
            raise GenerateError(f"{clip.name} has no video stream")
        size = (int(stream["width"]), int(stream["height"]))
        if size == (canvas.width, canvas.height):
            return size
        cover = f"scale={canvas.width}:{canvas.height}:force_original_aspect_ratio=increase:flags=lanczos"
        fitting = run_command(
            [
                tools.ffmpeg, "-v", "error", "-y", "-i", str(clip), "-an",
                "-vf", f"{cover},crop={canvas.width}:{canvas.height}",
                "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", str(fitted),
            ]
        )  # fmt: skip
    except (QAToolError, UnreadableMediaError) as error:
        raise GenerateError(f"cannot fit {clip.name} to the canvas: {error}") from error
    if fitting.returncode != 0:
        detail = (fitting.stderr.strip().splitlines() or ["ffmpeg failed"])[-1]
        raise GenerateError(f"cannot fit {clip.name} to the canvas: {detail}")
    os.replace(fitted, clip)
    return canvas.width, canvas.height


async def make_clip(
    still: Path,
    motion: str,
    out: Path,
    *,
    project: Path,
    seconds: int,
    topic_id: int | None = None,
    canvas: Canvas | None = None,
) -> dict:
    """Make one ``seconds`` clip from ``still`` (``motion`` says what moves) into ``out`` (a paid call), under the
    per-video cap counted for ``project``, scale it to ``canvas`` (default: the motion tokens' 1920x1080) and write
    its record. ``topic_id`` is the project's pipeline topic, kept in the record; the cap does not depend on it.

    A clip that cannot be scaled is kept as it came, with a ``warning`` in its record: it is paid for.

    Raises:
        ClipLengthError: the model cannot make a ``seconds`` clip; nothing was sent.
    """
    settings = load_i2v_settings()
    check_clip_seconds(seconds, settings.model)
    with tempfile.TemporaryDirectory(prefix="motion-clip-") as scratch:
        sent = Path(scratch) / f"{out.stem}.jpg"
        await asyncio.to_thread(save_shrunk_jpeg, still.read_bytes(), str(sent), STILL_EDGE)
        adapter = _adapter(settings, project / CLIP_LOG, Path(scratch))
        result = await adapter.generate_clip(
            sent,
            motion,
            settings.model,
            seconds,
            topic_id=CLIP_CAP_ID,
            clip_id=out.stem,
        )
        try:  # the clip was made and paid for: whatever fails from here on, it was billed
            out.parent.mkdir(parents=True, exist_ok=True)
            write_new(out, result.path)
        except Exception as error:
            error.may_be_billed = True
            raise
    try:
        fitted: dict[str, str] = {}
        try:
            width, height = await asyncio.to_thread(fit_to_canvas, out, canvas)
            fitted["size"] = f"{width}x{height}"
        except GenerateError as error:
            fitted["warning"] = str(error)
        return _record(
            out,
            made_by="animate",
            model=result.model,
            seconds=result.seconds,
            still=still.name,
            request=motion,
            topic_id=topic_id,
            cost_usd=result.cost_usd,
            **fitted,
        )
    except Exception as error:
        error.may_be_billed = True
        raise


def _adapter(settings: I2VSettings, log: Path, scratch: Path) -> I2VAdapter:
    return I2VAdapter(
        build_fal_client(settings),
        ClipBudget(settings.limits, log),
        settings=settings,
        visuals_root=scratch,
    )


def _record(out: Path, **made: object) -> dict:
    record = {
        "ai": True,
        "credit": AI_CREDIT,
        **made,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    atomic_write_bytes(out.with_name(out.name + ".json"), (json.dumps(record, indent=2) + "\n").encode("utf-8"))
    return record
