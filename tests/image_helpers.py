"""Helpers shared by the tests that need a small real image."""

from __future__ import annotations

from io import BytesIO

from PIL import Image


def image_bytes(
    color: str | tuple[int, int, int] = "white",
    fmt: str = "PNG",
    size: tuple[int, int] = (64, 36),
) -> bytes:
    """A tiny real image, encoded as ``fmt`` (a Pillow format name)."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()
