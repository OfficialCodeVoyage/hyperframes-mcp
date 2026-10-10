"""The app-compatibility tooling: reading an app.asar, the tool names its source quotes, and the diff script."""

from __future__ import annotations

import importlib.util
import json
import re
import struct
from pathlib import Path

import pytest

from hyperframes_mcp.server import bridge as bridge_module
from hyperframes_mcp.server.appinfo import (
    KNOWN_APP_TOOLS,
    REQUIRED_APP_TOOLS,
    RUN_SCOPED_APP_TOOLS,
    extract_asar,
    read_asar_file,
    source_tool_names,
)

ROOT = Path(__file__).resolve().parents[2]


def nested_asar(path: Path, files: dict[str, bytes], extra: dict | None = None) -> Path:
    """An ``app.asar`` with folders: ``files`` maps a path like ``main/a.mjs`` to its bytes."""
    tree: dict = {"files": {}}
    body, offset = b"", 0
    for name, data in files.items():
        node = tree
        *folders, leaf = name.split("/")
        for folder in folders:
            node = node["files"].setdefault(folder, {"files": {}})
        node["files"][leaf] = {"size": len(data), "offset": str(offset)}
        body += data
        offset += len(data)
    for name, entry in (extra or {}).items():
        tree["files"][name] = entry
    header = json.dumps(tree).encode()
    aligned = (len(header) + 3) & ~3
    path.write_bytes(struct.pack("<IIII", 4, 8 + aligned, 4 + aligned, len(header)) + header.ljust(aligned, b"\0") + body)
    return path


def script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tool_names_the_bridge_calls_are_the_required_ones_and_the_app_offers_them():
    source = Path(bridge_module.__file__).read_text(encoding="utf-8")
    called = {n for n in re.findall(r'"([a-z]+\.[A-Za-z]+)"', source) if not n.endswith(".json")}

    assert called == REQUIRED_APP_TOOLS
    assert REQUIRED_APP_TOOLS <= KNOWN_APP_TOOLS
    assert not RUN_SCOPED_APP_TOOLS & KNOWN_APP_TOOLS


def test_files_in_folders_of_an_asar_are_read_by_path(tmp_path):
    asar = nested_asar(tmp_path / "app.asar", {"main/a.mjs": b"one", "main/deep/b.mjs": b"two", "top.txt": b"three"})

    assert read_asar_file(asar, "main/deep/b.mjs") == b"two"
    assert read_asar_file(asar, "main/a.mjs") == b"one"
    assert read_asar_file(asar, "main/missing.mjs") is None


def test_extraction_writes_only_the_files_under_the_prefix(tmp_path):
    asar = nested_asar(tmp_path / "app.asar", {"main/a.mjs": b"one", "main/deep/b.mjs": b"two", "other/c.txt": b"x"})
    out = tmp_path / "out"

    written = extract_asar(asar, out, only="main/")

    assert sorted(written) == ["main/a.mjs", "main/deep/b.mjs"]
    assert (out / "main" / "deep" / "b.mjs").read_bytes() == b"two"
    assert not (out / "other").exists()


def test_extraction_refuses_a_path_that_leaves_the_folder(tmp_path):
    asar = nested_asar(tmp_path / "app.asar", {"main/ok.mjs": b"ok"}, extra={"..": {"files": {"evil.txt": {"size": 1, "offset": "0"}}}})
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="outside"):
        extract_asar(asar, out)

    assert not (tmp_path / "evil.txt").exists()


def test_extraction_skips_links_and_unpacked_files(tmp_path):
    asar = nested_asar(
        tmp_path / "app.asar",
        {"main/a.mjs": b"one"},
        extra={"link": {"link": "main/a.mjs"}, "native.node": {"size": 3, "offset": "0", "unpacked": True}},
    )

    assert extract_asar(asar, tmp_path / "out") == ["main/a.mjs"]


def test_source_tool_names_reads_quoted_names_of_the_mcp_files_only(tmp_path):
    asar = nested_asar(
        tmp_path / "app.asar",
        {
            "main/mcpHistoryTools.mjs": b'const T = { "history.undo": {}, "history.list": {} }; load("config.json")',
            "main/mcpSoundTools.mjs": b"const n = `sound.find`;",
            "main/agent.mjs": b'run("agent.run")',
            "shared/mcpX.mjs": b'"nope.here"',
        },
    )

    assert source_tool_names(asar) == {
        "history.list": ["mcpHistoryTools.mjs"],
        "history.undo": ["mcpHistoryTools.mjs"],
        "sound.find": ["mcpSoundTools.mjs"],
    }


def test_the_diff_is_clean_for_the_reviewed_list_and_names_what_changed():
    diff = script("diff_app_tools")

    clean, _ = diff.compare(set(KNOWN_APP_TOOLS), static=False)
    assert clean == []

    seen = (set(KNOWN_APP_TOOLS) - {"file.read", "media.list"}) | {"sound.find"}
    problems, _ = diff.compare(seen, static=False)
    text = "\n".join(problems)
    assert "file.read" in text and "calls tools the app no longer offers" in text
    assert "media.list" in text and "no longer offers" in text
    assert "sound.find" in text and "nobody has reviewed" in text


def test_a_run_scoped_name_is_accepted_when_reading_source_but_not_from_the_live_list():
    diff = script("diff_app_tools")
    seen = set(KNOWN_APP_TOOLS) | set(RUN_SCOPED_APP_TOOLS)

    assert diff.compare(seen, static=True)[0] == []
    assert diff.compare(seen, static=False)[0] != []
