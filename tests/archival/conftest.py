"""Archival tests never wait for a host's Retry-After or backoff; they read what would have been slept."""

import pytest

from hyperframes_mcp.archival import base


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """The waits the HTTP plumbing asked for, in order (nothing is slept for real)."""
    waited: list[float] = []

    async def record(seconds: float) -> None:
        waited.append(seconds)

    monkeypatch.setattr(base, "_sleep", record)
    return waited
