"""Small text helpers for provider metadata, which mixes strings, lists and HTML."""

import re
from html.parser import HTMLParser
from typing import Any

_PLACEHOLDER_DATE = re.compile(r"^[Xx?\-/\s]*$")
_DISPLAY_NONE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_VOID = frozenset({"br", "hr", "img", "input", "link", "meta", "wbr"})


class _TextExtractor(HTMLParser):
    """Collects the text a reader sees: script, style and elements hidden with ``display: none`` are left out (Wikimedia
    Commons hides a copy of "Unknown author" inside the visible one, which reads "Unknown authorUnknown author")."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0
        self._hidden_tag: str | None = None
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._hidden_tag is not None:
            if tag == self._hidden_tag:
                self._hidden_depth += 1
        elif tag not in _VOID and _DISPLAY_NONE.search(dict(attrs).get("style") or ""):
            self._hidden_tag, self._hidden_depth = tag, 1
        elif tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "li", "div"):
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self._hidden_tag is not None:
            if tag == self._hidden_tag:
                self._hidden_depth -= 1
                if self._hidden_depth == 0:
                    self._hidden_tag = None
        elif tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and self._hidden_tag is None:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html(value: str | None) -> str | None:
    """Plain text of an HTML fragment with whitespace collapsed, or ``None``."""
    if not value:
        return None
    extractor = _TextExtractor()
    extractor.feed(value)
    extractor.close()
    text = re.sub(r"\s+", " ", extractor.text()).strip()
    return text or None


def text_list(value: Any) -> list[str]:
    """Strings from a field that is a string or a list of strings."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def join_text(value: Any, separator: str = "; ") -> str | None:
    """Join a string or list of strings (HTML allowed) into one plain string."""
    parts = [t for item in text_list(value) if (t := strip_html(item))]
    return separator.join(dict.fromkeys(parts)) or None


def clean_date(value: Any) -> str | None:
    """A date string, or ``None`` for empty values and ``XXXX``-style placeholders."""
    text = join_text(value, " ")
    if text is None or _PLACEHOLDER_DATE.match(text):
        return None
    return text
