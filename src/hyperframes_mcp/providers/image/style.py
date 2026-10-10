"""The look every AI image prompt carries: the guide file, a profile's own wording, a default.

A prompt is ``<prefix><request><suffix>``. With no profile style (the usual case) the prefix is
the ``STYLE GUIDE:`` frame around the guide text, exactly as the service always built it::

    STYLE GUIDE:
    <guide text>

    IMAGE REQUEST:
    <request>

The guide text comes from, in order:

1. The style guide file the caller names (a markdown file). If the file has an image-prompt-base block
   (``<!-- image-prompt-base:start -->`` ... ``<!-- image-prompt-base:end -->``, the text inside
   the fence), only that block is used, because the rest of the file is a human guide. That
   works while the guide is still a DRAFT and carries the approval marker line. A file without
   the block is used whole, unless it still holds the placeholder marker (an unfilled template).
2. ``default_style_guide(niche)`` (``DEFAULT_STYLE_GUIDE`` for the default profile), only when
   the file gives no style text at all.

Comments, code fences and ``DRAFT (edit me)`` tags are stripped, so none of them reach a paid
image call.

A profile sets its own wording in ``~/.hyperframes-mcp/config/profiles/<name>/image_style.yaml``::

    prefix: "Engraving of a 19th-century exchange floor. Scene: "   # replaces the whole frame
    suffix: ". No text, no lettering."                                # follows the request

Either key is optional.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from hyperframes_mcp.motion.profiles import DEFAULT_NICHE

logger = logging.getLogger(__name__)

_DEFAULT_STYLE_GUIDE = (
    "Cinematic documentary style, dramatic lighting, historically accurate, "
    "rich color palette with warm tones, photorealistic quality, "
    "suitable for {a_niche} documentary"
)


def with_article(niche: str) -> str:
    """The niche with its indefinite article: ``economic history`` -> ``an economic history``."""
    article = "an" if niche[0].lower() in "aeiou" else "a"
    return f"{article} {niche}"


def default_style_guide(niche: str = DEFAULT_NICHE) -> str:
    """The built-in style text, naming the profile's niche."""
    return _DEFAULT_STYLE_GUIDE.format(a_niche=with_article(niche))


DEFAULT_STYLE_GUIDE = default_style_guide()

PLACEHOLDER_MARKER = "TODO:"  # a style guide line or file that still has this is a template, not a style

PROFILE_STYLE_FILE = "image_style.yaml"
_PROFILE_STYLE_KEYS = frozenset({"prefix", "suffix"})

_PROMPT_BASE_BLOCK = re.compile(
    r"<!--\s*image-prompt-base:start\s*-->(.*?)<!--\s*image-prompt-base:end\s*-->",
    re.DOTALL,
)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_DRAFT_TAG = re.compile(r"[ \t]*DRAFT \(edit me\)")


@dataclass(frozen=True)
class ImageStyle:
    """Wording around the image request. ``prefix`` None means the style-guide frame."""

    prefix: str | None = None
    suffix: str = ""

    @classmethod
    def from_mapping(cls, data: dict, source: object) -> "ImageStyle":
        """Build from ``{"prefix": ..., "suffix": ...}`` parsed out of a YAML file.

        Raises:
            ValueError: ``data`` has other keys, or a value that is not text. ``source``
                names the file in the message.
        """
        unknown = sorted(set(data) - _PROFILE_STYLE_KEYS)
        if unknown:
            raise ValueError(f"Unknown keys in {source}: {', '.join(unknown)}")
        for key, value in data.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{source}: {key} must be text")
        return cls(prefix=data.get("prefix"), suffix=data.get("suffix") or "")

    def render(self, request: str, guide_text: str) -> str:
        """The full prompt for ``request``."""
        prefix = self.prefix
        if prefix is None:
            prefix = f"STYLE GUIDE:\n{guide_text}\n\nIMAGE REQUEST:\n"
        return f"{prefix}{request}{self.suffix}"


def load_image_style(config_dir: Path | None) -> ImageStyle:
    """Read ``image_style.yaml`` from a profile's config bundle.

    No directory, no file or an empty file all mean the default style.

    Raises:
        ValueError: the file has keys other than ``prefix`` and ``suffix``, or a value that
            is not text.
    """
    if config_dir is None:
        return ImageStyle()
    path = Path(config_dir) / PROFILE_STYLE_FILE
    if not path.is_file():
        return ImageStyle()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ImageStyle.from_mapping(data, path)


def load_style_guide_text(path: Path) -> str | None:
    """The style text the guide file at ``path`` offers, or None if it offers none."""
    try:
        content = Path(path).read_text()
    except FileNotFoundError:
        logger.warning("Style guide not found at %s, using default", path)
        return None

    block = _PROMPT_BASE_BLOCK.search(content)
    if block:
        text = _clean_block(block.group(1))
        if not text:
            logger.warning("Style guide %s has an empty image-prompt-base block", path)
        return text or None

    if PLACEHOLDER_MARKER in content:
        logger.warning("Style guide contains placeholder content, using default")
        return None
    text = _DRAFT_TAG.sub("", _HTML_COMMENT.sub("", content))
    return text if text.strip() else None


def resolve_style_guide(path: Path | None, niche: str = DEFAULT_NICHE) -> str:
    """The guide text for prompts: the file's style text, else the built-in one for ``niche``."""
    return (load_style_guide_text(path) if path else None) or default_style_guide(niche)


def _clean_block(raw: str) -> str:
    kept = []
    for line in _HTML_COMMENT.sub("", raw).splitlines():
        if line.strip().startswith("```") or PLACEHOLDER_MARKER in line:
            continue
        kept.append(_DRAFT_TAG.sub("", line).rstrip())
    return "\n".join(kept).strip()
