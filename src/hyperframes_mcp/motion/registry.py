"""The motion registry: the library's items, and an installer that copies them into a HyperFrames project.

``assets/motion/registry.json`` lists the items in the shape of a HyperFrames registry item (name, type, title,
description, license, files, registryDependencies, dimensions and duration for blocks) plus three fields of
ours: ``variables`` (what a shot list may fill), ``source`` (where a vendored item came from, ``null`` for our own)
and ``notes``. ``hyperframes add`` only takes an https registry, so :func:`install` does the copy itself; nothing
here touches the network.

An install writes the item's files and, once per project, what every component needs: ``vendor/gsap.min.js``,
``tokens.css`` (generated from the tokens) and the font files the component names through ``var(--font-*)``.
``motion.lock.json`` keeps the sha256 of every file written, so a later install can tell a file the user edited
from one that is merely out of date and never overwrites the first without ``force=True``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from hyperframes_mcp.motion import tokens as token_module
from hyperframes_mcp.motion.tokens import HEX_COLOUR, is_number
from hyperframes_mcp.util.atomic import atomic_write_bytes
from hyperframes_mcp.util.paths import node_dir

MOTION_DIR = token_module.MOTION_DIR
REGISTRY_PATH = MOTION_DIR / "registry.json"
LOCK_NAME = "motion.lock.json"
GSAP_TARGET = "vendor/gsap.min.js"
TOKENS_CSS_TARGET = "tokens.css"

ITEM_TYPES = ("hyperframes:block", "hyperframes:component")
FILE_TYPES = (
    "hyperframes:composition",
    "hyperframes:snippet",
    "hyperframes:asset",
    "hyperframes:style",
)
VARIABLE_TYPES = ("string", "number", "color", "boolean", "enum")

_TOP_KEYS = {"name", "items"}
_ITEM_KEYS = {
    "name",
    "type",
    "title",
    "description",
    "license",
    "files",
    "registryDependencies",
    "variables",
    "source",
    "notes",
}
_BLOCK_KEYS = {
    "dimensions",
    "duration",
    "coversFrame",
    "needsCredit",
    "slotNotChecked",
}
_VARIABLE_KEYS = {
    "id",
    "type",
    "label",
    "default",
    "placeholder",
    "maxLength",
    "min",
    "max",
    "step",
    "unit",
    "options",
}
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SPDX = re.compile(
    r"^(?:LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.+-]+)(?: (?:AND|OR|WITH) (?:LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.+-]+))*$"
)
_VARIABLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


def gsap_source() -> Path:
    """GSAP's browser build, from the pinned install (``hyperframes-mcp setup``). It is not shipped with this package:
    GSAP has its own licence (https://gsap.com/standard-license).

    Raises:
        RegistryError: GSAP is not installed.
    """
    path = node_dir() / "node_modules" / "gsap" / "dist" / "gsap.min.js"
    if not path.is_file():
        raise RegistryError(
            "GSAP is not installed: run `hyperframes-mcp setup` (it installs the pinned HyperFrames CLI and GSAP)"
        )
    return path


class RegistryError(ValueError):
    """The registry (or an install) is not valid; the message says which item or file and why."""


@dataclass(frozen=True)
class RegistryFile:
    """One file of an item: where it lives in the library and where it is installed."""

    path: str
    target: str
    type: str


@dataclass(frozen=True)
class Source:
    """Where a vendored item came from: a url and the pinned commit."""

    url: str
    sha: str


@dataclass(frozen=True)
class Item:
    """One registry item (a block or a component)."""

    name: str
    type: str
    title: str
    description: str
    license: str
    files: tuple[RegistryFile, ...]
    registry_dependencies: tuple[str, ...]
    dimensions: dict[str, int] | None
    duration: float | None
    variables: tuple[dict[str, Any], ...]
    source: Source | None
    notes: str
    # a block that paints an opaque ground over the whole frame: whatever is below it in the same shot is hidden
    covers_frame: bool = False
    # a block that shows a picture or clip from elsewhere: a credit-line layer with text must be on screen with it
    needs_credit: bool = False
    # why the shortest slot of the block cannot be computed before a render (None: the preflight computes it, or the
    # static durationSeconds minimum is the whole rule)
    slot_not_checked: str | None = None


@dataclass(frozen=True)
class Registry:
    """The validated registry: its items by name and the folder the item files are read from."""

    name: str
    items: dict[str, Item]
    root: Path

    def get(self, name: str) -> Item:
        """The item called ``name``; the error lists what there is."""
        if name not in self.items:
            available = ", ".join(sorted(self.items)) or "none"
            raise RegistryError(f"no item {name!r} in the registry; available: {available}")
        return self.items[name]


@dataclass
class InstallReport:
    """What an install did, by absolute path: files written, files kept because the user changed them, and files already current."""

    written: list[Path] = field(default_factory=list)
    kept: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)


# -- paths ---------------------------------------------------------------------------------------


def _unsafe_path(value: object) -> str | None:
    """Why ``value`` is not a safe relative path (no absolute path, no ``..``, no empty segment), else None."""
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty relative path"
    if "\\" in value or re.match(r"^[A-Za-z]:", value) or value.startswith("/"):
        return f"{value!r} must be a relative path with forward slashes"
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"{value!r} has an empty, '.' or '..' segment"
    return None


# -- loading -------------------------------------------------------------------------------------


def _keys(raw: Any, allowed: set[str], required: set[str], path: str, errors: list[str]) -> bool:
    if not isinstance(raw, dict):
        errors.append(f"{path}: must be an object")
        return False
    for key in sorted(set(raw) - allowed):
        errors.append(f"{path}.{key}: unknown key")
    for key in sorted(required - set(raw)):
        errors.append(f"{path}.{key}: missing")
    return True


def _check_variable(raw: Any, path: str, errors: list[str]) -> None:
    if not _keys(raw, _VARIABLE_KEYS, {"id", "type", "label", "default"}, path, errors):
        return
    if not (isinstance(raw.get("id"), str) and _VARIABLE_ID.match(raw["id"])):
        errors.append(f"{path}.id: must be a name like holdSeconds")
    kind = raw.get("type")
    if kind not in VARIABLE_TYPES:
        errors.append(f"{path}.type: must be one of {', '.join(VARIABLE_TYPES)}")
        return
    if not isinstance(raw.get("label"), str) or not raw["label"].strip():
        errors.append(f"{path}.label: must be a non-empty string")
    if "default" not in raw:
        return
    default = raw["default"]
    if kind == "enum":
        options = raw.get("options")
        values = (
            [o.get("value") for o in options if isinstance(o, dict)] if isinstance(options, list) and options else None
        )
        if values is None or len(values) != len(options):
            errors.append(f"{path}.options: an enum needs a list of {{value, label}}")
        elif default not in values:
            errors.append(f"{path}.default: {default!r} is not one of the options")
        return
    ok = {
        "string": isinstance(default, str),
        "number": is_number(default),
        "boolean": isinstance(default, bool),
        "color": isinstance(default, str) and bool(HEX_COLOUR.match(default)),
    }[kind]
    if not ok:
        errors.append(f"{path}.default: {default!r} is not a {kind}")


def _parse_item(raw: Any, root: Path, errors: list[str], index: int) -> Item | None:
    where = f"items[{index}]"
    if not _keys(raw, _ITEM_KEYS | _BLOCK_KEYS, _ITEM_KEYS, where, errors):
        return None
    name = raw.get("name")
    if isinstance(name, str) and _NAME.match(name):
        where = f"item {name!r}"
    else:
        errors.append(f"{where}.name: must be kebab-case")
    before = len(errors)
    kind = raw.get("type")
    if kind not in ITEM_TYPES:
        errors.append(f"{where}.type: must be one of {', '.join(ITEM_TYPES)}")
    for key in ("title", "description"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            errors.append(f"{where}.{key}: must be a non-empty string")
    licence = raw.get("license")
    if not (isinstance(licence, str) and _SPDX.match(licence)):
        errors.append(f"{where}.license: must be an SPDX identifier or a LicenseRef-...")

    files: list[RegistryFile] = []
    raw_files = raw.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        errors.append(f"{where}.files: must be a non-empty list")
    else:
        for i, entry in enumerate(raw_files):
            at = f"{where}.files[{i}]"
            if not _keys(
                entry,
                {"path", "target", "type"},
                {"path", "target", "type"},
                at,
                errors,
            ):
                continue
            problems = [f"{at}.{key}: {why}" for key in ("path", "target") if (why := _unsafe_path(entry.get(key)))]
            if entry.get("type") not in FILE_TYPES:
                problems.append(f"{at}.type: must be one of {', '.join(FILE_TYPES)}")
            if not problems and not (root / entry["path"]).is_file():
                problems.append(f"{at}.path: {entry['path']} does not exist under {root}")
            errors += problems
            if not problems:
                files.append(RegistryFile(entry["path"], entry["target"], entry["type"]))

    dependencies = raw.get("registryDependencies")
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        errors.append(f"{where}.registryDependencies: must be a list of item names")
        dependencies = []

    dimensions = duration = None
    if kind == "hyperframes:block":
        dims = raw.get("dimensions")
        if (
            isinstance(dims, dict)
            and set(dims) == {"width", "height"}
            and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in dims.values())
        ):
            dimensions = dict(dims)
        else:
            errors.append(f"{where}.dimensions: a block needs {{width, height}} in pixels")
        duration = raw.get("duration")
        if not is_number(duration) or duration <= 0:
            errors.append(f"{where}.duration: a block needs a positive number of seconds")
            duration = None
        for flag in ("coversFrame", "needsCredit"):
            if flag in raw and not isinstance(raw[flag], bool):
                errors.append(f"{where}.{flag}: must be true or false")
        reason = raw.get("slotNotChecked")
        if "slotNotChecked" in raw and not (isinstance(reason, str) and reason.strip()):
            errors.append(f"{where}.slotNotChecked: must say why, as text")
    elif kind == "hyperframes:component":
        for key in sorted(_BLOCK_KEYS & set(raw)):
            errors.append(f"{where}.{key}: only a block has {key}")

    variables = raw.get("variables")
    if not isinstance(variables, list):
        errors.append(f"{where}.variables: must be a list")
        variables = []
    seen_ids: set[str] = set()
    for i, variable in enumerate(variables):
        _check_variable(variable, f"{where}.variables[{i}]", errors)
        if isinstance(variable, dict):
            if variable.get("id") in seen_ids:
                errors.append(f"{where}.variables[{i}].id: {variable['id']!r} appears twice")
            seen_ids.add(variable.get("id"))

    source = None
    raw_source = raw.get("source")
    if raw_source is not None:
        if (
            isinstance(raw_source, dict)
            and set(raw_source) == {"url", "sha"}
            and isinstance(raw_source["url"], str)
            and raw_source["url"].startswith("https://")
            and isinstance(raw_source["sha"], str)
            and _SHA.match(raw_source["sha"])
        ):
            source = Source(raw_source["url"], raw_source["sha"])
        else:
            errors.append(f"{where}.source: must be null or {{url (https), sha (40 hex)}}")
    notes = raw.get("notes")
    if not isinstance(notes, str):
        errors.append(f"{where}.notes: must be a string")
        notes = ""
    elif raw_source is not None and not notes.strip():
        errors.append(f"{where}.notes: a vendored item must say what was changed")

    if len(errors) > before or not isinstance(name, str):
        return None
    return Item(
        name=name,
        type=kind,
        title=raw["title"],
        description=raw["description"],
        license=licence,
        files=tuple(files),
        registry_dependencies=tuple(dependencies),
        dimensions=dimensions,
        duration=float(duration) if duration is not None else None,
        variables=tuple(variables),
        source=source,
        notes=notes,
        covers_frame=raw.get("coversFrame", False),
        needs_credit=raw.get("needsCredit", False),
        slot_not_checked=raw.get("slotNotChecked"),
    )


def _cross_checks(items: dict[str, Item]) -> list[str]:
    errors: list[str] = []
    targets: dict[str, str] = {}
    for item in items.values():
        for dep in item.registry_dependencies:
            if dep not in items:
                errors.append(f"item {item.name!r}: registryDependencies names {dep!r}, which is not in the registry")
        for entry in item.files:
            if entry.target in targets and targets[entry.target] != item.name:
                errors.append(f"items {targets[entry.target]!r} and {item.name!r} have the same target {entry.target}")
            targets[entry.target] = item.name
    if errors:
        return errors

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            cycle = " -> ".join((*path[path.index(name) :], name))
            raise RegistryError(f"invalid motion registry: circular registryDependencies: {cycle}")
        for dep in items[name].registry_dependencies:
            visit(dep, (*path, name))

    for name in items:
        visit(name, ())
    return []


def parse_registry(raw: Any, root: Path = MOTION_DIR) -> Registry:
    """Validate a decoded registry document; item files are looked for under ``root``.

    Raises :class:`RegistryError` listing every problem found.
    """
    errors: list[str] = []
    if not _keys(raw, _TOP_KEYS, _TOP_KEYS, "registry", errors):
        raise RegistryError("invalid motion registry:\n  " + "\n  ".join(errors))
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        errors.append("registry.name: must be a non-empty string")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        errors.append("registry.items: must be a list")
        raw_items = []
    items: dict[str, Item] = {}
    for index, entry in enumerate(raw_items):
        item = _parse_item(entry, root, errors, index)
        if item is None:
            continue
        if item.name in items:
            errors.append(f"item {item.name!r} is listed twice")
        items[item.name] = item
    if not errors:
        errors += _cross_checks(items)
    if errors:
        raise RegistryError("invalid motion registry:\n  " + "\n  ".join(errors))
    return Registry(name=raw["name"], items=items, root=Path(root))


def load_registry(path: Path = REGISTRY_PATH, root: Path = MOTION_DIR) -> Registry:
    """Read and validate a registry file (the shipped one by default)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise RegistryError(f"{path}: not valid JSON: {err}") from err
    return parse_registry(raw, root)


@lru_cache(maxsize=1)
def default_registry() -> Registry:
    """The shipped registry, loaded once."""
    return load_registry()


def list_items(reg: Registry | None = None) -> list[Item]:
    """Every item, by name."""
    reg = reg or default_registry()
    return [reg.items[name] for name in sorted(reg.items)]


def get(name: str, reg: Registry | None = None) -> Item:
    """The item called ``name``."""
    return (reg or default_registry()).get(name)


# -- install -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Planned:
    target: str
    data: bytes
    owner: str


def _resolve_order(reg: Registry, name: str) -> list[Item]:
    """The item preceded by its dependencies, each once, dependencies first."""
    ordered: list[Item] = []

    def visit(item_name: str) -> None:
        item = reg.get(item_name)
        if item in ordered:
            return
        for dep in item.registry_dependencies:
            visit(dep)
        ordered.append(item)

    visit(name)
    return ordered


def _plan(reg: Registry, items: list[Item]) -> list[_Planned]:
    """Every file an install of ``items`` writes: the items' own, then the shared ones."""
    planned: dict[str, _Planned] = {}
    texts: list[str] = []
    for item in items:
        for entry in item.files:
            if why := _unsafe_path(entry.target):
                raise RegistryError(f"item {item.name!r}: target {why}")
            if why := _unsafe_path(entry.path):
                raise RegistryError(f"item {item.name!r}: path {why}")
            data = (reg.root / entry.path).read_bytes()
            planned.setdefault(entry.target, _Planned(entry.target, data, item.name))
            if entry.path.endswith(".html"):
                texts.append(data.decode("utf-8"))

    t = token_module.default_tokens()
    shared = {
        GSAP_TARGET: gsap_source().read_bytes(),
        TOKENS_CSS_TARGET: token_module.stylesheet(t).encode("utf-8"),
    }
    for role in token_module.font_roles_in("\n".join(texts)):
        for name in t.fonts[role].asset_files:
            shared[f"{token_module.FONT_TARGET_DIR}/{name}"] = (token_module.FONTS_DIR / name).read_bytes()
    for target, data in shared.items():
        planned.setdefault(target, _Planned(target, data, "motion"))
    return list(planned.values())


def _read_lock(project: Path) -> dict[str, dict[str, str]]:
    path = project / LOCK_NAME
    if not path.exists():
        return {}
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
        files = lock["files"]
        if lock["version"] != 1 or not isinstance(files, dict):
            raise ValueError("unknown layout")
        return {str(k): dict(v) for k, v in files.items()}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
        raise RegistryError(f"{path}: not a valid {LOCK_NAME} ({err}); fix or delete it") from err


def _write_lock(project: Path, files: dict[str, dict[str, str]]) -> None:
    text = json.dumps({"version": 1, "files": dict(sorted(files.items()))}, indent=2)
    atomic_write_bytes(project / LOCK_NAME, (text + "\n").encode("utf-8"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_HF_ID = re.compile(rb' data-hf-id="[^"]*"')


def _without_hf_ids(data: bytes) -> bytes:
    """``data`` without the ``data-hf-id`` stamps the HyperFrames app and CLI add to a page's elements: they are the
    app's bookkeeping, not an edit of the person's."""
    return _HF_ID.sub(b"", data)


class _Markup(HTMLParser):
    """The tags, attributes and text of a page. The app re-saves a page it opens (attributes re-quoted, the doctype
    moved, white space changed, ``data-hf-id`` added), so none of those count: only what a person could have edited."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[Any, ...]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        kept = sorted((k, v) for k, v in attrs if k != "data-hf-id")
        self.tokens.append(("start", tag, tuple(kept)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        self.tokens.append(("end", tag))

    def handle_data(self, data: str) -> None:
        if text := " ".join(data.split()):
            self.tokens.append(("text", text))


def _markup(data: bytes) -> list[tuple[Any, ...]]:
    parser = _Markup()
    parser.feed(data.decode("utf-8", "replace"))
    return parser.tokens


def install_item(
    name: str,
    project_dir: str | Path,
    force: bool = False,
    registry: Registry | None = None,
) -> InstallReport:
    """Install ``name`` (and its dependencies and shared files) into a HyperFrames project directory.

    A file that exists and differs from the library's copy is overwritten only when it still matches the hash
    the lock recorded (the library moved on, the user did not) or when ``force`` is true; otherwise it is kept and
    listed in ``kept``. What the HyperFrames app does to a page it opens is not a difference: its ``data-hf-id`` stamps,
    and a page re-saved with the same markup (other quoting, white space or doctype position). Every target is
    checked to stay inside the project, links included.
    """
    reg = registry or default_registry()
    project = Path(project_dir).resolve()
    plan = _plan(reg, _resolve_order(reg, name))
    lock = _read_lock(project)
    files = dict(lock)
    report = InstallReport()

    destinations = [(entry, project / entry.target) for entry in plan]
    for entry, dest in destinations:
        if not dest.resolve().is_relative_to(project):
            raise RegistryError(f"target {entry.target} resolves outside the project directory {project}")
    for entry, dest in destinations:
        new_sha = _sha256(entry.data)
        if dest.exists():
            if not dest.is_file():
                raise RegistryError(f"{dest} exists and is not a file")
            on_disk = dest.read_bytes()
            current, plain = _sha256(on_disk), _sha256(_without_hf_ids(on_disk))
            if new_sha in (current, plain) or (dest.suffix == ".html" and _markup(on_disk) == _markup(entry.data)):
                files[entry.target] = {"sha256": new_sha, "item": entry.owner}
                report.unchanged.append(dest)
                continue
            if not force and lock.get(entry.target, {}).get("sha256") not in (
                current,
                plain,
            ):
                report.kept.append(dest)
                continue
        atomic_write_bytes(dest, entry.data)
        files[entry.target] = {"sha256": new_sha, "item": entry.owner}
        report.written.append(dest)

    if files != lock:
        _write_lock(project, files)
    return report


def install(
    name: str,
    project_dir: str | Path,
    force: bool = False,
    registry: Registry | None = None,
) -> list[Path]:
    """Install ``name`` into ``project_dir`` and return the paths written (see :func:`install_item`)."""
    return install_item(name, project_dir, force, registry).written
