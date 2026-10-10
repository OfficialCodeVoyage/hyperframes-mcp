"""A profile: the settings one kind of video shares (a profile, a client, a series).

A profile is a folder, ``~/.hyperframes-mcp/config/profiles/<name>/``, with optional files:

- ``edit.yaml``: how the server edits a project of the profile (``motion.edit_config``);
- ``image_style.yaml``: the wording around a picture request (``providers.image.style``);
- ``profile.yaml``: ``niche`` (spliced into the built-in style text) and ``style_guide`` (a text file in the folder
  whose ``image-prompt-base`` block, or whole text, is the picture style).

A profile without a folder gets the defaults, so nothing needs setting up. The profile's name is the
``profile_name`` of a project's ``video.project.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from hyperframes_mcp.util.paths import user_config_dir

DEFAULT_PROFILE = "default"
DEFAULT_NICHE = "documentary"
PROFILE_FILE = "profile.yaml"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX_LENGTH = 63


@dataclass(frozen=True)
class Profile:
    """One profile: its name, its folder (which may not exist), its niche and its style guide file (or None)."""

    name: str
    config_dir: Path
    niche: str = DEFAULT_NICHE
    style_guide_path: Path | None = None


def profiles_dir() -> Path:
    """Where the profiles are (not created)."""
    return user_config_dir() / "profiles"


def load_profile(name: str = DEFAULT_PROFILE) -> Profile:
    """The profile ``name``: its folder and what ``profile.yaml`` says, else the defaults.

    Raises:
        ValueError: the name is not lower-case words joined by hyphens, or ``profile.yaml`` is not valid.
    """
    if not NAME_PATTERN.fullmatch(name) or len(name) > NAME_MAX_LENGTH:
        raise ValueError(
            f"{name!r} is not a profile name: use lower-case letters and digits joined by hyphens (at most "
            f"{NAME_MAX_LENGTH} characters)"
        )
    folder = profiles_dir() / name
    path = folder / PROFILE_FILE
    if not path.is_file():
        return Profile(name, folder)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ValueError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(data, dict) or set(data) - {"niche", "style_guide"}:
        raise ValueError(f"{path} may hold only `niche` and `style_guide`")
    niche = str(data.get("niche") or DEFAULT_NICHE).strip()
    if "{" in niche or "}" in niche:
        raise ValueError(f"{path}: niche must not contain braces")
    guide = data.get("style_guide")
    guide_path = None
    if guide:
        guide_path = folder / str(guide)
        if Path(str(guide)).is_absolute() or ".." in Path(str(guide)).parts:
            raise ValueError(f"{path}: style_guide must be a file inside the profile's folder")
    return Profile(name, folder, niche, guide_path)
