"""``hyperframes-mcp doctor`` and the pieces it reads: the app's build from its asar, the CLI install, the checks."""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path

from hyperframes_mcp.hfcli import install
from hyperframes_mcp.server import doctor
from hyperframes_mcp.server.appinfo import KNOWN_APP_TOOLS, TESTED_BUILDS, installed_build, read_asar_file


def make_asar(path: Path, files: dict[str, bytes]) -> Path:
    """A small ``app.asar``: a JSON header of sizes and offsets, then the files."""
    entries, offset, body = {}, 0, b""
    for name, data in files.items():
        entries[name] = {"size": len(data), "offset": str(offset)}
        body += data
        offset += len(data)
    header = json.dumps({"files": entries}).encode()
    aligned = (len(header) + 3) & ~3
    prefix = struct.pack("<IIII", 4, 8 + aligned, 4 + aligned, len(header))
    path.write_bytes(prefix + header.ljust(aligned, b"\0") + body)
    return path


def make_app(root: Path, build: str | None) -> Path:
    package = {"name": "hyperframes-desktop", "hfDatadog": {"version": build}} if build else {"name": "x"}
    asar = root / "Contents" / "Resources" / "app.asar"
    asar.parent.mkdir(parents=True)
    make_asar(asar, {"package.json": json.dumps(package).encode(), "other.txt": b"hello"})
    return root


def test_a_file_is_read_out_of_an_asar_by_its_name(tmp_path):
    asar = make_asar(tmp_path / "app.asar", {"a.txt": b"first", "b.txt": b"second"})

    assert read_asar_file(asar, "b.txt") == b"second"
    assert read_asar_file(asar, "a.txt") == b"first"
    assert read_asar_file(asar, "missing.txt") is None


def test_the_build_is_the_version_the_app_reports_to_its_monitor_not_the_bundle_version(tmp_path):
    app = make_app(tmp_path / "HyperFrames.app", "b296")

    assert installed_build(app) == "b296"


def test_no_app_or_no_build_number_gives_none(tmp_path):
    assert installed_build(tmp_path / "nothing") is None
    assert installed_build(make_app(tmp_path / "plain.app", None)) is None


def test_the_app_check_is_green_for_a_tried_build_and_a_warning_for_a_new_one(tmp_path):
    tried = doctor.check_app_build(make_app(tmp_path / "a.app", TESTED_BUILDS[-1]))
    new = doctor.check_app_build(make_app(tmp_path / "b.app", "b999"))
    absent = doctor.check_app_build(tmp_path / "none.app")

    assert tried.status == "ok"
    assert new.status == "warn" and "b999" in new.detail and "app-compat" in new.detail
    assert absent.status == "warn"


def test_the_registration_check_finds_the_command_the_entry_runs(tmp_path):
    python = tmp_path / "python"
    python.write_text("")
    shown = f"x:\n  Type: stdio\n  Command: {python}\n  Args: -m hyperframes_mcp serve\n"

    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=shown, stderr="")

    ok = doctor.check_registration("x", run)
    python.unlink()
    gone = doctor.check_registration("x", run)

    assert ok.status == "ok" and str(python) in ok.detail
    assert gone.status == "fail" and "does not exist" in gone.detail


def test_an_unregistered_server_is_a_warning_that_names_the_command(monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such server")

    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/bin/claude")

    check = doctor.check_registration("x", run)

    assert check.status == "warn" and "hyperframes-mcp register" in check.detail


def test_the_keys_check_names_the_keys_and_never_shows_a_value(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr(doctor.keys, "_from_keychain", lambda name: None)

    check = doctor.check_keys()

    assert check.status == "ok" and "OPENAI_API_KEY" in check.detail
    assert "sk-secret-value" not in check.detail


def test_the_report_counts_failures_and_the_exit_code_ignores_warnings():
    checks = [doctor.Check("a", "ok", "fine"), doctor.Check("b", "warn", "hm"), doctor.Check("c", "fail", "bad")]

    assert doctor.report(checks).splitlines()[-1] == "1 failed, 1 to look at"
    assert doctor.exit_code(checks) == 1
    assert doctor.exit_code(checks[:2]) == 0
    assert doctor.report(checks[:1]).splitlines()[-1] == "all good"


def test_setup_copies_the_pinned_files_and_runs_npm_ci_there(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(install, "node_major", lambda node: 24)

    def runner(argv, **kwargs):
        seen["argv"], seen["cwd"] = argv, Path(kwargs["cwd"])
        script = install.cli_script(seen["cwd"])
        script.parent.mkdir(parents=True)
        script.write_text("")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    script = install.install(tmp_path / "node", log=lambda line: None, runner=runner)

    assert seen["argv"][:2] == ["/bin/npm", "ci"] and seen["cwd"] == tmp_path / "node"
    assert (tmp_path / "node" / "package.json").is_file() and (tmp_path / "node" / "package-lock.json").is_file()
    assert script == install.cli_script(tmp_path / "node")


def test_setup_says_what_to_install_when_node_is_too_old_or_npm_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(install, "node_major", lambda node: 18)
    try:
        install.install(tmp_path / "a", log=lambda line: None)
    except install.SetupError as error:
        assert "Node 22" in str(error)
    else:
        raise AssertionError("expected SetupError")

    monkeypatch.setattr(install, "node_major", lambda node: 24)

    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="ERR! network")

    try:
        install.install(tmp_path / "b", log=lambda line: None, runner=failing)
    except install.SetupError as error:
        assert "npm ci failed" in str(error) and "network" in str(error)
    else:
        raise AssertionError("expected SetupError")


def test_the_apps_tool_names_are_read_with_tools_list(bridge, app):
    import asyncio

    assert asyncio.run(bridge.tool_names()) == sorted(KNOWN_APP_TOOLS)
    app.offered = ["comments.post", "file.read"]
    assert asyncio.run(bridge.tool_names()) == ["comments.post", "file.read"]


def test_a_tool_the_reviewed_build_did_not_have_is_named_in_a_warning(bridge, app):
    assert doctor.check_app_tools(bridge).status == "ok"

    app.offered = [*app.offered, "video.generate"]
    new = doctor.check_app_tools(bridge)
    app.offered = [name for name in KNOWN_APP_TOOLS if name != "file.write"]
    gone = doctor.check_app_tools(bridge)

    assert new.status == "warn" and "video.generate" in new.detail and "spend" in new.detail
    assert gone.status == "warn" and "file.write" in gone.detail


def test_the_spend_check_shows_the_limits_and_warns_when_frozen_or_held(monkeypatch):
    from hyperframes_mcp.motion import spend

    assert doctor.check_spend().status == "ok"
    spend.freeze()
    try:
        frozen = doctor.check_spend()
    finally:
        spend.thaw()

    assert frozen.status == "warn" and "frozen" in frozen.detail
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_CALL_USD", "lots")
    assert doctor.check_spend().status == "fail"
