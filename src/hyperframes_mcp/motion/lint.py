"""Static lint of a motion component against the component contract.

A component is one HTML file: a ``data-composition-*`` root, a ``<style>``, and one script that builds a paused
GSAP timeline and registers it under the composition id. The linter reads the text only (it never runs the
page) and checks the rules that keep a render deterministic and on brand: no network, no clock or randomness,
no CSS motion, no layout tweens, only token colours, fonts, eases and type sizes. The rules and the reasons are in
``docs/motion-library.md``.

It is a text scanner, not a parser. Comments are blanked first (so a URL or ``Math.random()`` in a comment is
fine), strings are respected when JavaScript comments are found, and a regular-expression literal that contains a
quote can confuse that. A line opts out of one code with a trailing ``/* lint-allow: CODE reason */`` (in CSS or
JavaScript) or ``<!-- lint-allow: CODE reason -->`` (in HTML); the reason is mandatory.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from hyperframes_mcp.motion.tokens import FONT_ROLES, GENERIC_FAMILIES, Tokens, default_tokens

CODES = (
    "NETWORK_URL",
    "RANDOM_OR_CLOCK",
    "BARE_GSAP",
    "NOT_PAUSED",
    "NO_TIMELINE_REGISTRATION",
    "TWEEN_FROM",
    "BANNED_EASE",
    "CSS_TRANSITION",
    "LAYOUT_TWEEN",
    "FILTER_TWEEN",
    "OFF_TOKEN_COLOR",
    "SMALL_TEXT",
    "FONT_NOT_IN_TOKENS",
    "MISSING_VARIABLES",
    "MISSING_ROOT_ATTRS",
    "ALLOW_WITHOUT_REASON",
)

# Eases allowed on top of the four in tokens.json (ease section): quiet in/out shapes and the gentle outs.
_EXTRA_EASES = ("power1.inOut", "power2.out", "power4.out", "expo.in", "expo.inOut")
_LINEAR_EASES = ("none", "linear", "power0.none")
_LINEAR_MARK = "data-linear-ok"
_NAMESPACE_URL = ("http://www.w3.org/", "https://www.w3.org/")
_CSS_WIDE = ("inherit", "initial", "unset", "revert")
_ROOT_ATTRS = (
    "data-composition-id",
    "data-width",
    "data-height",
    "data-start",
    "data-duration",
)

# One left-to-right scan, so whatever opens first wins: an HTML comment hides a <script> written inside it, and
# a "<!--" inside a script string is script text.
_REGION = re.compile(
    r"<!--.*?-->|<style\b[^>]*>(?P<style>.*?)</style\s*>|<script\b[^>]*>(?P<script>.*?)</script\s*>",
    re.DOTALL | re.IGNORECASE,
)
_TAG = re.compile(r"""<[A-Za-z][\w:-]*(?:\s+[^\s"'<>/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'<>]+))?)*\s*/?>""")
_ATTR = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>]+)))?""")
_STYLE_ATTR = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")
_ALLOW = re.compile(r"lint-allow\s*:\s*([A-Z][A-Z_]*)[ \t]*([^\n]*?)\s*(?:\*/|-->|$)", re.MULTILINE)

_URL = re.compile(r"""(?<![\w])https?://[^\s"'<>)\\]+""", re.IGNORECASE)
_PROTOCOL_RELATIVE = re.compile(
    r"""(?:\b(?:src|href|srcset|poster|action)\s*=\s*|url\(\s*|@import\s+(?:url\(\s*)?)["']?\s*(//[^\s"')]+)""",
    re.IGNORECASE,
)
_CLOCK = re.compile(
    r"\b(Math\s*\.\s*random|Date\s*\.\s*now|performance\s*\.\s*now|requestAnimationFrame|setTimeout|setInterval)\s*\("
    r"|\bnew\s+Date\s*\(\s*\)"
)
_BARE_GSAP = re.compile(r"\bgsap\s*\.\s*(to|from|fromTo)\s*\(")
_GSAP_TIMELINE = re.compile(r"\bgsap\s*\.\s*timeline\s*\(")
_TIMELINE_VAR = re.compile(r"\b([A-Za-z_$][\w$]*)\s*=\s*gsap\s*\.\s*timeline\b")
_REGISTRATION = re.compile(r"__timelines\s*(?:\[[^\]]+\]|\.\s*[\w$]+)\s*=(?!=)")
_EASE = re.compile(r"""\bease\s*:\s*(["'])([^"']*)\1""")
_CSS_MOTION = re.compile(
    r"(?<![-\w])(?:transition|animation)(?:-[a-z-]+)?\s*:|@(?:-webkit-)?keyframes\b",
    re.IGNORECASE,
)
_JS_CSS_MOTION = re.compile(r"\.\s*style\s*\.\s*(?:transition|animation)\w*\s*=")
_ARRAY_CALL = re.compile(r"\bArray\s*\.\s*(?:to|from|fromTo)\s*\(")
_TWEEN_CALL = re.compile(r"\.\s*(to|from|fromTo)\s*\(")
_KEY = re.compile(r"""(["']?)([A-Za-z_$][\w$-]*)\1\s*:""")
_LAYOUT_KEY = re.compile(r"width|height|top|left|right|bottom|font-?[sS]ize|(?:margin|padding)(?:[A-Z]\w*|-\w+)?")
_FILTER_KEY = re.compile(r"filter|blur|backdropFilter|backdrop-filter|webkitFilter|-webkit-filter")
_HEX = re.compile(r"(?<![\w&#])#([0-9a-fA-F]+)(?![\w-])")
_COLOR_FUNCTION = re.compile(r"(?<![\w-])(rgba?|hsla?)\(\s*([^)]*)\)", re.IGNORECASE)
_COLOR_ATTR = re.compile(
    r"""\b(?:fill|stroke|stop-color|flood-color|lighting-color|color|bgcolor)\s*=\s*(["'])\s*(#[0-9a-fA-F]+|(?:rgba?|hsla?)\([^)]*\))\s*\1""",
    re.IGNORECASE,
)
_COLOR_KEY_BEFORE = re.compile(
    r"(?:color|colour|fill|stroke|background\w*|border\w*|outline\w*|shadow\w*)\s*[:=,(]\s*$",
    re.IGNORECASE,
)
_URL_FUNCTION = re.compile(r"url\([^)]*\)", re.IGNORECASE)
_FONT_SIZE = re.compile(r"(?<![-\w])font-size\s*:\s*([\d.]+)px", re.IGNORECASE)
_FONT_SHORTHAND = re.compile(r"(?<![-\w])font\s*:\s*[^;]*?(?<![\w.-])([\d.]+)px", re.IGNORECASE)
_FONT_FAMILY = re.compile(r"(?<![-\w])font-family\s*:\s*([^;}]+)", re.IGNORECASE)
_FONT_FAMILY_ATTR = re.compile(r"""\bfont-family\s*=\s*(["'])([^"']*)\1""", re.IGNORECASE)
_FONT_FAMILY_JS = re.compile(r"""\bfontFamily\s*[:=]\s*(["'])([^"']*)\1""")


@dataclass(frozen=True)
class Problem:
    """One thing a component does that the contract forbids: a code, the line and what to do instead."""

    code: str
    line: int
    message: str
    path: str = ""

    def __str__(self) -> str:
        return f"{self.path or '<text>'}:{self.line}: {self.code} {self.message}"


# -- reading the text ---------------------------------------------------------------------------


def _blank(text: str) -> str:
    """``text`` with every character but newlines turned into a space, so offsets and lines survive."""
    return re.sub(r"[^\n]", " ", text)


def _mask_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: _blank(m.group()), css, flags=re.DOTALL)


def _skip_string(text: str, i: int) -> int:
    """The index after the string literal that opens at ``text[i]``."""
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
        elif text[i] == quote:
            return i + 1
        else:
            i += 1
    return len(text)


def _mask_js_comments(js: str) -> str:
    """``js`` with ``//`` and ``/* */`` comments blanked; a ``//`` inside a string is not a comment."""
    out = list(js)
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if c in "\"'`":
            i = _skip_string(js, i)
            continue
        if c == "/" and js[i + 1 : i + 2] in ("/", "*"):
            if js[i + 1] == "/":
                end = js.find("\n", i)
                end = n if end < 0 else end
            else:
                end = js.find("*/", i + 2)
                end = n if end < 0 else end + 2
            out[i:end] = _blank(js[i:end])
            i = end
            continue
        i += 1
    return "".join(out)


def _skip_balanced(text: str, i: int) -> int:
    """The index after the bracket that closes the ``(``, ``[`` or ``{`` at ``text[i]``."""
    depth = 0
    while i < len(text):
        c = text[i]
        if c in "\"'`":
            i = _skip_string(text, i)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _object_keys(text: str, start: int) -> list[tuple[str, int]]:
    """The keys (and where they are) of the object literal that opens at ``text[start]``.

    Only the object's own keys count; ``css: {...}`` is the one nested object that is looked into, because
    GSAP treats its keys as properties of the target.
    """
    keys: list[tuple[str, int]] = []
    i, expect_key = start + 1, True
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if expect_key:
            expect_key = False
            m = _KEY.match(text, i)
            if m:
                keys.append((m.group(2), i))
                i = m.end()
                if m.group(2) == "css":
                    j = i
                    while j < len(text) and text[j].isspace():
                        j += 1
                    if text[j : j + 1] == "{":
                        keys += _object_keys(text, j)
                continue
        if c in "\"'`":
            i = _skip_string(text, i)
        elif c in "([{":
            i = _skip_balanced(text, i)
        elif c == "}":
            return keys
        else:
            if c == ",":
                expect_key = True
            i += 1
    return keys


@dataclass(frozen=True)
class _Rule:
    """A CSS rule or an inline style: the selector text, the declarations and where they start."""

    selector: str
    body: str
    offset: int


class _Source:
    """A component's text with comments blanked, and the regions the checks look at."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]
        buf = list(text)
        self.styles: list[tuple[int, int]] = []
        self.scripts: list[tuple[int, int]] = []
        for m in _REGION.finditer(text):
            if m.group("style") is not None:
                a, b = m.span("style")
                self.styles.append((a, b))
                buf[a:b] = _mask_css_comments(text[a:b])
            elif m.group("script") is not None:
                a, b = m.span("script")
                self.scripts.append((a, b))
                buf[a:b] = _mask_js_comments(text[a:b])
            else:
                buf[m.start() : m.end()] = _blank(m.group())
        self.masked = "".join(buf)

    def line(self, offset: int) -> int:
        """The 1-based line of a character offset."""
        return bisect.bisect_right(self._starts, offset)

    def js(self) -> list[tuple[int, str]]:
        """Each script body and its offset."""
        return [(a, self.masked[a:b]) for a, b in self.scripts]

    def rules(self) -> list[_Rule]:
        """The CSS rules of the style blocks and the inline styles (with the element's class and id as selector)."""
        found = []
        for a, b in self.styles:
            for m in _RULE.finditer(self.masked, a, b):
                found.append(_Rule(m.group(1).strip(), m.group(2), m.start(2)))
        for tag in _TAG.finditer(self.masked):
            attr = _STYLE_ATTR.search(tag.group())
            if attr:
                group = 1 if attr.group(1) is not None else 2
                names = " ".join(re.findall(r"""\b(?:class|id)\s*=\s*["']([^"']*)["']""", tag.group()))
                found.append(_Rule(names, attr.group(group), tag.start() + attr.start(group)))
        return found


Found = Iterator[tuple[str, int, str]]  # code, line, message


# -- the checks ---------------------------------------------------------------------------------


def _network(src: _Source, tokens: Tokens) -> Found:
    message = "{!r} is a network reference: vendor the file into the project and use a relative path"
    for m in _URL.finditer(src.masked):
        if not m.group().lower().startswith(_NAMESPACE_URL):
            yield "NETWORK_URL", src.line(m.start()), message.format(m.group())
    for m in _PROTOCOL_RELATIVE.finditer(src.masked):
        yield "NETWORK_URL", src.line(m.start(1)), message.format(m.group(1))


def _clock(src: _Source, tokens: Tokens) -> Found:
    for base, js in src.js():
        for m in _CLOCK.finditer(js):
            name = re.sub(r"\s+", " ", m.group()).rstrip("( ").strip()
            yield (
                "RANDOM_OR_CLOCK",
                src.line(base + m.start()),
                (
                    f"{name} makes a frame depend on the machine or the moment: a render must be a pure function of the time"
                ),
            )


def _bare_gsap(src: _Source, tokens: Tokens) -> Found:
    for base, js in src.js():
        for m in _BARE_GSAP.finditer(js):
            yield (
                "BARE_GSAP",
                src.line(base + m.start()),
                (f"gsap.{m.group(1)}() runs on the global clock: put the tween on the paused timeline (tl.fromTo)"),
            )


def _timeline_creation(src: _Source, tokens: Tokens) -> Found:
    registered = False
    first_timeline = first_script = None
    for base, js in src.js():
        first_script = first_script or base
        for m in _GSAP_TIMELINE.finditer(js):
            first_timeline = first_timeline if first_timeline is not None else base + m.start()
            args = js[m.end() - 1 : _skip_balanced(js, m.end() - 1)]
            if not re.search(r"\bpaused\s*:\s*true\b", args):
                yield (
                    "NOT_PAUSED",
                    src.line(base + m.start()),
                    ("gsap.timeline() must be created with { paused: true }: the renderer seeks it, it must not play"),
                )
        registered = registered or bool(_REGISTRATION.search(js))
    if not registered:
        where = first_timeline if first_timeline is not None else first_script or 0
        yield (
            "NO_TIMELINE_REGISTRATION",
            src.line(where),
            ('no window.__timelines["<composition-id>"] = tl: the renderer finds the timeline there'),
        )


def _tween_from(src: _Source, tokens: Tokens) -> Found:
    names: set[str] = set()
    for _, js in src.js():
        names |= set(_TIMELINE_VAR.findall(js))
    pattern = re.compile(rf"(?:\b(?:{'|'.join(sorted(names or {'tl'}))})|\))\s*\.\s*from\s*\(")
    for base, js in src.js():
        for m in pattern.finditer(js):
            yield (
                "TWEEN_FROM",
                src.line(base + m.start()),
                (
                    "tl.from() reads the target's current state when the timeline is built: use tl.fromTo() with both ends"
                ),
            )


def _eases(src: _Source, tokens: Tokens) -> Found:
    allowed = set(tokens.ease.values()) | set(_EXTRA_EASES)
    linear_ok = _LINEAR_MARK in src.masked
    for base, js in src.js():
        for m in _EASE.finditer(js):
            ease = m.group(2).strip()
            if ease in allowed or (ease in _LINEAR_EASES and linear_ok):
                continue
            if ease in _LINEAR_EASES:
                why = f"linear motion is for a ticker that carries the {_LINEAR_MARK} attribute"
            elif ease.split(".")[0].split("(")[0] in ("back", "elastic", "bounce"):
                why = "it overshoots or springs: documentary motion settles, it does not bounce"
            else:
                why = "it is not one of the library's eases"
            yield (
                "BANNED_EASE",
                src.line(base + m.start(2)),
                (f'ease "{ease}": {why}; use the token eases ({", ".join(sorted(allowed))})'),
            )


def _css_motion(src: _Source, tokens: Tokens) -> Found:
    message = "CSS transitions and animations run on the wall clock: animate on the timeline instead"
    for a, b in src.styles:
        for m in _CSS_MOTION.finditer(src.masked, a, b):
            yield "CSS_TRANSITION", src.line(m.start()), message
    for rule in src.rules():
        if any(a <= rule.offset < b for a, b in src.styles):
            continue
        for m in _CSS_MOTION.finditer(rule.body):
            yield "CSS_TRANSITION", src.line(rule.offset + m.start()), message
    for base, js in src.js():
        for m in _JS_CSS_MOTION.finditer(js):
            yield "CSS_TRANSITION", src.line(base + m.start()), message


def _tween_properties(src: _Source, tokens: Tokens) -> Found:
    for base, js in src.js():
        array_calls = {m.end() for m in _ARRAY_CALL.finditer(js)}
        for call in _TWEEN_CALL.finditer(js):
            if call.end() in array_calls:
                continue
            open_paren = call.end() - 1
            end = _skip_balanced(js, open_paren)
            i = open_paren + 1
            while i < end - 1:
                if js[i] == "{":
                    for key, at in _object_keys(js, i):
                        if _LAYOUT_KEY.fullmatch(key):
                            yield (
                                "LAYOUT_TWEEN",
                                src.line(base + at),
                                (
                                    f'tweening "{key}" re-flows the layout every frame: animate x, y, scale, clipPath or opacity'
                                ),
                            )
                        elif _FILTER_KEY.fullmatch(key):
                            yield (
                                "FILTER_TWEEN",
                                src.line(base + at),
                                (
                                    f'tweening "{key}" is not reproducible between renders: use opacity or a prepared image'
                                ),
                            )
                    i = _skip_balanced(js, i)
                elif js[i] in "\"'`":
                    i = _skip_string(js, i)
                elif js[i] in "([":
                    i = _skip_balanced(js, i)
                else:
                    i += 1


def _normalized_hex(digits: str) -> str | None:
    """The ``#RRGGBB`` of a 3, 4, 6 or 8 digit hex colour (alpha dropped), None for any other length."""
    if len(digits) in (3, 4):
        digits = "".join(c * 2 for c in digits[:3])
    elif len(digits) in (6, 8):
        digits = digits[:6]
    else:
        return None
    return "#" + digits.upper()


def _literal_colours(text: str, tokens_hex: set[str], *, is_js: bool = False) -> Iterable[tuple[int, str]]:
    """The colour literals in ``text`` that are not a token value, as (offset, literal)."""
    plain = _URL_FUNCTION.sub(lambda m: _blank(m.group()), text)  # url(#fragment) is not a colour
    for m in _HEX.finditer(plain):
        value = _normalized_hex(m.group(1))
        if value is None or value in tokens_hex:
            continue
        if is_js:
            before = plain[: m.start()]
            literal_start = before[-1:] in ("'", '"', "`")
            # "#add" as a whole string is as likely a selector as a colour: only a colour-like key decides
            if literal_start and len(m.group(1)) in (3, 4) and not _COLOR_KEY_BEFORE.search(before[:-1][-40:]):
                continue
        yield m.start(), m.group()
    for m in _COLOR_FUNCTION.finditer(plain):
        parts = [p for p in re.split(r"[,\s/]+", m.group(2).strip()) if p]
        if m.group(1).lower().startswith("rgb"):
            channels = parts[:3]
            if len(channels) < 3 or not all(re.fullmatch(r"\d{1,3}", c) for c in channels):
                continue  # built from a variable: not a literal
            if all(int(c) <= 255 for c in channels):
                value = "#" + "".join(f"{int(c):02X}" for c in channels)
                if value in tokens_hex:
                    continue
        yield m.start(), m.group()


def _colours(src: _Source, tokens: Tokens) -> Found:
    tokens_hex = {c.upper() for c in tokens.colors.values()}
    message = "colour literal {} is not a token: use var(--color-...) or a color-mix() of one"
    for rule in src.rules():
        for at, literal in _literal_colours(rule.body, tokens_hex):
            yield "OFF_TOKEN_COLOR", src.line(rule.offset + at), message.format(literal)
    for base, js in src.js():
        for at, literal in _literal_colours(js, tokens_hex, is_js=True):
            yield "OFF_TOKEN_COLOR", src.line(base + at), message.format(literal)
    for m in _COLOR_ATTR.finditer(src.masked):
        for _, literal in _literal_colours(m.group(2), tokens_hex):
            yield "OFF_TOKEN_COLOR", src.line(m.start(2)), message.format(literal)


def _small_text(src: _Source, tokens: Tokens) -> Found:
    for rule in src.rules():
        credit = re.search(r"credit|source", rule.selector, re.IGNORECASE)
        floor = tokens.sizes["min_credit" if credit else "min_text"]
        for pattern in (_FONT_SIZE, _FONT_SHORTHAND):
            for m in pattern.finditer(rule.body):
                if float(m.group(1)) < floor:
                    yield (
                        "SMALL_TEXT",
                        src.line(rule.offset + m.start(1)),
                        (
                            f"{m.group(1)}px text is under the {floor}px minimum"
                            f"{' for a credit or source line' if credit else ''}: use var(--size-...)"
                        ),
                    )


def _family_problems(value: str, tokens: Tokens) -> list[str]:
    """The font names in a ``font-family`` value that are not a token family, a generic or a --font variable."""
    families = {f.family.lower() for f in tokens.fonts.values()}
    bad = []
    for item in value.split(","):
        name = item.strip().strip("'\"").strip()
        low = name.lower()
        if not low or low in families or low in GENERIC_FAMILIES or low in _CSS_WIDE or low.startswith("var(--font-"):
            continue
        bad.append(name)
    return bad


def _fonts(src: _Source, tokens: Tokens) -> Found:
    variables = ", ".join(f"var(--font-{role})" for role in FONT_ROLES)

    def report(offset: int, value: str) -> Found:
        for name in _family_problems(value, tokens):
            yield (
                "FONT_NOT_IN_TOKENS",
                src.line(offset),
                (f"font {name!r} is not a token family: use one of {variables}"),
            )

    for rule in src.rules():
        for m in _FONT_FAMILY.finditer(rule.body):
            yield from report(rule.offset + m.start(1), m.group(1))
    for m in _FONT_FAMILY_ATTR.finditer(src.masked):
        yield from report(m.start(2), m.group(2))
    for base, js in src.js():
        for m in _FONT_FAMILY_JS.finditer(js):
            yield from report(base + m.start(2), m.group(2))


def _attributes(tag: str) -> dict[str, str]:
    """The attributes of one opening tag (a bare attribute has the value ``""``)."""
    attrs: dict[str, str] = {}
    for m in _ATTR.finditer(tag, re.match(r"<[\w:-]+", tag).end()):
        attrs[m.group(1)] = next((g for g in m.groups()[1:] if g is not None), "")
    return attrs


def _root(src: _Source, tokens: Tokens) -> Found:
    tags = [(t, _attributes(t.group())) for t in _TAG.finditer(src.masked)]
    root = next(((t, a) for t, a in tags if "data-composition-id" in a), None)
    page = next((a for t, a in tags if t.group().lower().startswith("<html")), {})
    root_attrs = root[1] if root else {}
    line = src.line(root[0].start()) if root else 1
    if root is None:
        yield (
            "MISSING_ROOT_ATTRS",
            1,
            "no element has data-composition-id: the component needs a composition root",
        )
    for name in _ROOT_ATTRS if root else ():
        if name not in root_attrs:
            yield "MISSING_ROOT_ATTRS", line, f"the composition root lacks {name}"
    for name, want in (
        ("data-width", tokens.canvas["width"]),
        ("data-height", tokens.canvas["height"]),
    ):
        if name in root_attrs and root_attrs[name] != str(want):
            yield (
                "MISSING_ROOT_ATTRS",
                line,
                f'{name}="{root_attrs[name]}" must be "{want}", the canvas of the tokens',
            )
    declared_on = (root_attrs | page) if root else {k: "" for _, a in tags for k in a}
    if "data-composition-variables" not in declared_on:
        yield (
            "MISSING_VARIABLES",
            line,
            (
                "no data-composition-variables on <html> or the composition root: "
                "every setting a shot list fills is declared there"
            ),
        )


_CHECKS = (
    _network,
    _clock,
    _bare_gsap,
    _timeline_creation,
    _tween_from,
    _eases,
    _css_motion,
    _tween_properties,
    _colours,
    _small_text,
    _fonts,
    _root,
)


# -- entry points -------------------------------------------------------------------------------


def _allowances(src: _Source) -> tuple[dict[int, set[str]], list[tuple[int, str]]]:
    """The lines that opt out of a code, and the markers that gave no reason (line, code)."""
    allowed: dict[int, set[str]] = {}
    unexplained: list[tuple[int, str]] = []
    for m in _ALLOW.finditer(src.text):
        line = src.line(m.start())
        if re.search(r"\w", m.group(2)):
            allowed.setdefault(line, set()).add(m.group(1))
        else:
            unexplained.append((line, m.group(1)))
    return allowed, unexplained


def lint_text(text: str, path: str = "", tokens: Tokens | None = None) -> list[Problem]:
    """Lint the text of one component; problems come back in line order.

    ``path`` is only carried into each :class:`Problem`.
    """
    tokens = tokens or default_tokens()
    src = _Source(text)
    allowed, unexplained = _allowances(src)
    found: set[Problem] = set()
    for check in _CHECKS:
        for code, line, message in check(src, tokens):
            if code not in allowed.get(line, set()):
                found.add(Problem(code, line, message, path))
    for line, code in unexplained:
        found.add(
            Problem(
                "ALLOW_WITHOUT_REASON",
                line,
                f"lint-allow: {code} needs a reason after the code; without one it excuses nothing",
                path,
            )
        )
    return sorted(found, key=lambda p: (p.line, p.code, p.message))


def lint_file(path: str | Path) -> list[Problem]:
    """Lint one component file."""
    path = Path(path)
    return lint_text(path.read_text(encoding="utf-8"), str(path))


def lint_tree(root: str | Path) -> list[Problem]:
    """Lint every ``.html`` file under ``root``, file by file in path order."""
    problems: list[Problem] = []
    for path in sorted(Path(root).rglob("*.html")):
        problems += lint_file(path)
    return problems
