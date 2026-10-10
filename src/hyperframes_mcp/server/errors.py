"""Which failures a tool reports to the agent in its own words.

The SDK shows the agent the message of a ``ToolError`` and hides any other exception behind "Error executing tool"
(a crash stays on the server). Our library's own errors are written for the person or agent who asked: the allowed
clip lengths, the limit a confirm would pass, the credit a picture needs. So every tool turns them into ``ToolError``;
anything else is a crash and stays hidden.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError

from hyperframes_mcp.motion.edit_config import EditConfigError
from hyperframes_mcp.motion.frames import FrameError
from hyperframes_mcp.motion.generate import GenerateError
from hyperframes_mcp.motion.hf_cli import HyperframesError
from hyperframes_mcp.motion.manifest import ManifestError
from hyperframes_mcp.motion.media import MediaError
from hyperframes_mcp.motion.project_edit import EditError
from hyperframes_mcp.motion.registry import RegistryError
from hyperframes_mcp.motion.spend import SpendError
from hyperframes_mcp.motion.timing import AlignmentError
from hyperframes_mcp.motion.vectors import VectorError
from hyperframes_mcp.providers.video.errors import I2VError
from hyperframes_mcp.server.bridge import AppError
from hyperframes_mcp.server.project import ProjectError
from hyperframes_mcp.server.selection import SelectionError

EXPECTED = (
    AlignmentError,
    AppError,
    EditConfigError,
    EditError,
    FrameError,
    GenerateError,
    HyperframesError,
    I2VError,
    ManifestError,
    MediaError,
    ProjectError,
    RegistryError,
    SelectionError,
    SpendError,
    VectorError,
)
Tool = TypeVar("Tool", bound=Callable[..., Any])


def reported(tool: Tool) -> Tool:
    """``tool``, with our library's errors raised as ``ToolError`` so the agent reads their message."""
    if inspect.iscoroutinefunction(tool):

        @functools.wraps(tool)
        async def run_async(*args: Any, **kwargs: Any) -> Any:
            try:
                return await tool(*args, **kwargs)
            except EXPECTED as error:
                raise ToolError(str(error)) from error

        return run_async  # type: ignore[return-value]

    @functools.wraps(tool)
    def run(*args: Any, **kwargs: Any) -> Any:
        try:
            return tool(*args, **kwargs)
        except EXPECTED as error:
            raise ToolError(str(error)) from error

    return run  # type: ignore[return-value]
