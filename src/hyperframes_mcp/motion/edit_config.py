"""A profile's settings for editing a composed video: ``~/.hyperframes-mcp/config/profiles/<name>/edit.yaml``.

The file is optional (see ``motion.profiles``). A profile without one gets the defaults below, which carry no house
rules: a profile's rules (its look, how long a credit shows) are in its own file, so another profile never inherits
them. The MCP server reads it for a project of the profile (the ``profile_name`` of its ``video.project.json``):

    picture_quality: high   # what quote_picture asks for when the call names no quality: medium or high
    picture_format: jpg     # the file a new picture is written as: jpg (light, about 1 MB) or png (lossless)
    house_rules: []         # the profile's own rules, which the chat follows (project_info and style_info give them)
    style_references: []    # stills that show the profile's look; NOT USED yet (reference pictures are a later, paid option)
    search:                 # search_media
      sources: [loc, wikimedia, internet_archive, openverse]   # which sources, and the order their hits come in
      allow_by: false       # whether CC BY pictures may be found when the call does not say

A value that is present but wrong, or a key that is not one of these, is an error that names the file and the key, never
silently a default. The spend limits stay in ``limits.yaml`` (``spend.profiles.<name>``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
    field_validator,
)

from hyperframes_mcp.motion.profiles import DEFAULT_PROFILE, load_profile
from hyperframes_mcp.motion.project_edit import PICTURE_QUALITIES
from hyperframes_mcp.util.yaml_io import load_yaml

EDIT_FILE = "edit.yaml"
Source = Literal["loc", "wikimedia", "internet_archive", "openverse"]
SOURCES: tuple[str, ...] = get_args(Source)
# the suffixes of a picture file ``hyperframes_mcp.motion.generate.PICTURE_FORMATS`` writes, without the dot
PICTURE_FORMAT_CHOICES = ("jpg", "png")
HOUSE_RULES_MAX = 20
HOUSE_RULE_CHARS = 600


class EditConfigError(ValueError):
    """``edit.yaml`` cannot be read, or holds a wrong value or an unknown key; the message names the file and the key."""


def _inside_the_bundle(path: str) -> str:
    """A still of the profile's own folder: relative, and never up out of it."""
    if Path(path).is_absolute() or ".." in Path(path).parts:
        raise ValueError(f"{path!r} must be a path inside the profile's folder")
    return path


class SearchPreferences(BaseModel):
    """What ``search_media`` does for the profile when the call does not say."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: list[Source] = Field(default_factory=lambda: list(SOURCES), min_length=1)
    allow_by: StrictBool = False

    @field_validator("sources")
    @classmethod
    def _each_source_once(cls, sources: list[str]) -> list[str]:
        if len(set(sources)) != len(sources):
            raise ValueError("a source is named twice")
        return sources


class EditConfig(BaseModel):
    """One profile's edit settings; every key is optional."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    picture_quality: str = "high"
    picture_format: Literal["jpg", "png"] = "jpg"
    house_rules: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=HOUSE_RULE_CHARS),
        ]
    ] = Field(default_factory=list, max_length=HOUSE_RULES_MAX)
    style_references: list[Annotated[str, AfterValidator(_inside_the_bundle)]] = Field(default_factory=list)
    search: SearchPreferences = Field(default_factory=SearchPreferences)

    @field_validator("picture_quality")
    @classmethod
    def _a_quality_the_picture_code_makes(cls, quality: str) -> str:
        if quality not in PICTURE_QUALITIES:
            raise ValueError(f"must be one of {', '.join(PICTURE_QUALITIES)}")
        return quality


def edit_file(profile_name: str = DEFAULT_PROFILE) -> Path:
    """Where the profile's ``edit.yaml`` is (or would be): in its folder.

    Raises:
        EditConfigError: the name is not a profile name, or the profile's ``profile.yaml`` cannot be read.
    """
    try:
        return load_profile(profile_name).config_dir / EDIT_FILE
    except ValueError as error:
        raise EditConfigError(f"profile {profile_name!r}: {error}") from error


def existing_edit_file(profile_name: str = DEFAULT_PROFILE) -> str | None:
    """The path of the profile's ``edit.yaml`` as text when it has one, else None (it gets the defaults).

    Raises:
        EditConfigError: as ``edit_file``.
    """
    path = edit_file(profile_name)
    return str(path) if path.is_file() else None


def load_edit_config(profile_name: str = DEFAULT_PROFILE) -> EditConfig:
    """The profile's edit settings; the defaults when its bundle has no ``edit.yaml``.

    Raises:
        EditConfigError: the file is not valid YAML or not a mapping, holds an unknown key or a wrong value, or the slug
            is not a profile name.
    """
    path = edit_file(profile_name)
    if not path.is_file():
        return EditConfig()
    try:
        data: Any = load_yaml(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise EditConfigError(f"{path} is not valid YAML: {error}") from error
    if data is None:
        return EditConfig()
    if not isinstance(data, dict):
        raise EditConfigError(f"{path} must be a mapping of settings")
    try:
        return EditConfig.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in error.errors())
        raise EditConfigError(f"{path}: {problems}") from error
