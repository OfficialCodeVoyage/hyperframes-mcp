"""Credential variables read from the environment, and the error for one that is not set.

Neutral on purpose: the dashboard, the voice pipeline and the Shorts wiring all import it, and none
of them may import another. The error text is the one ``src.runner.advice`` recognises, so a stage
that fails for a missing key gets the same advice whichever of them raised it.
"""

from __future__ import annotations

import os


class MissingKeyError(Exception):
    """A credential variable is not set. The text is the one a failed stage records, so the runner's
    failure advice recognises it."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} environment variable is not set")
        self.name = name


def get_key(name: str) -> str:
    """The value of the environment variable ``name``, read now.

    Raises:
        MissingKeyError: it is unset or empty.
    """
    value = os.getenv(name)
    if not value:
        raise MissingKeyError(name)
    return value
