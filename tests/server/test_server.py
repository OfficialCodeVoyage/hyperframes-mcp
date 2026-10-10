"""The server as a client sees it: its rules, its tools and what the ping tool shows (key names, never values)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

import pytest
from mcp import Client

from hyperframes_mcp.server import __main__ as cli
from hyperframes_mcp.server import version
from hyperframes_mcp.server.server import INSTRUCTIONS, KEY_NAMES, NAME, build_server


@pytest.mark.asyncio
async def test_a_client_gets_the_house_rules_and_a_read_only_ping(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/videos/gold")

    async with Client(build_server(), raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        result = await client.call_tool("ping", {})
        instructions = client.instructions

    assert instructions == INSTRUCTIONS
    assert tools["ping"].annotations.read_only_hint is True
    shown = result.structured_content
    assert shown["server"] == NAME and shown["project_dir"] == "/videos/gold"
    assert shown["keys"] == {"OPENAI_API_KEY": True, "FAL_KEY": False} | {
        name: False for name in KEY_NAMES if name not in ("OPENAI_API_KEY", "FAL_KEY")
    }
    assert "sk-test-not-a-real-key" not in str(result)


@pytest.mark.asyncio
async def test_ping_shows_which_code_runs_so_a_stale_server_is_seen(monkeypatch):
    monkeypatch.setattr(version, "STARTED_SHA", "abc1234")

    async with Client(build_server(), raise_exceptions=True) as client:
        shown = (await client.call_tool("ping", {})).structured_content["version"]

    assert shown["git_sha"] == "abc1234"
    assert shown["pid"] == os.getpid()
    assert datetime.fromisoformat(shown["started_at"]).tzinfo is not None


def test_the_rules_ask_the_agent_to_say_so_and_have_the_app_restarted_when_the_tools_are_missing():
    rules = " ".join(INSTRUCTIONS.split())

    assert "not in your tool list" in rules and "say so in your answer" in rules
    assert "quit the HyperFrames app completely (Cmd+Q) and reopen the project" in rules
    assert "never fall back to it silently" in rules


def test_the_commit_is_that_of_the_repository_and_none_without_git_or_a_repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = [
        "git",
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True)
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()
    bare = tmp_path / "not-a-repo"
    bare.mkdir()

    assert head and version.git_sha(repo) == head
    assert version.git_sha(bare) is None
    monkeypatch.setenv("PATH", str(tmp_path))
    assert version.git_sha(repo) is None


def test_register_runs_the_installed_package_at_user_scope_under_the_servers_name():
    command = cli.register_command(python="/venv/bin/python")

    assert command[:7] == [
        "claude",
        "mcp",
        "add",
        "--scope",
        "user",
        "--transport",
        "stdio",
    ]
    assert command[7] == NAME
    assert "-e" not in command
    assert command[command.index("--") + 1 :] == [
        "/venv/bin/python",
        "-m",
        "hyperframes_mcp",
        "serve",
    ]


def test_register_dev_puts_the_src_folder_of_a_checkout_on_the_path(tmp_path):
    command = cli.register_command(python="/venv/bin/python", dev=tmp_path)

    assert command[command.index("-e") + 1] == f"PYTHONPATH={tmp_path.resolve() / 'src'}"
    assert command.index("-e") < command.index("--")


class FakeClaude:
    """Stands in for the claude command: notes every call and succeeds."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def test_register_replaces_the_earlier_entry_and_says_to_restart_the_app(monkeypatch, capsys):
    claude = FakeClaude()
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(cli.subprocess, "run", claude)

    assert cli.main(["register"]) == 0

    assert claude.calls[0] == ["claude", "mcp", "remove", "--scope", "user", NAME]
    assert claude.calls[1][:3] == ["claude", "mcp", "add"]
    out = capsys.readouterr().out
    assert f"registered {NAME}" in out and "Cmd+Q" in out


def test_register_with_app_adds_the_apps_own_server_and_never_shows_its_token(monkeypatch, capsys, app):
    from hyperframes_mcp.server import bridge

    claude = FakeClaude()
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(cli.subprocess, "run", claude)
    monkeypatch.setattr(bridge, "SUPPORT_DIR", app.support)

    assert cli.main(["register", "--with-app"]) == 0

    added = [c for c in claude.calls if c[:3] == ["claude", "mcp", "add"] and cli.APP_SERVER_NAME in c]
    assert len(added) == 1 and added[0][-1] == f"http://127.0.0.1:{app.port}/mcp?token={app.token}"
    shown = capsys.readouterr()
    assert app.token not in shown.out and app.token not in shown.err


def test_register_with_app_says_so_when_the_app_has_never_run(monkeypatch, capsys, tmp_path):
    from hyperframes_mcp.server import bridge

    claude = FakeClaude()
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(cli.subprocess, "run", claude)
    monkeypatch.setattr(bridge, "SUPPORT_DIR", tmp_path / "no-app")

    assert cli.main(["register", "--with-app"]) == 1
    assert "has not run" in capsys.readouterr().err


def test_register_without_the_claude_command_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    assert cli.main(["register"]) == 1
    assert "claude command is not on PATH" in capsys.readouterr().err


def test_adopt_writes_a_manifest_with_absolute_sources_and_will_not_overwrite_one(tmp_path, capsys):
    from hyperframes_mcp.motion.manifest import file_sha256, read_manifest

    project, source = tmp_path / "project", tmp_path / "source"
    project.mkdir()
    source.mkdir()
    (project / "index.html").write_text("<html></html>")
    (source / "shotlist.json").write_text("{}")
    (source / "words.json").write_text("[]")
    argv = ["adopt", str(project), "--profile", "history", "--topic", "4"]
    argv += [
        "--shotlist",
        str(source / "shotlist.json"),
        "--alignment",
        str(source / "words.json"),
    ]

    assert cli.main(argv) == 0
    written = read_manifest(project)
    assert (written.profile, written.topic_id) == ("history", 4)
    assert written.alignment == (source / "words.json").resolve()
    assert written.shotlist_sha256 == file_sha256(source / "shotlist.json")

    assert cli.main(argv) == 1
    assert "already has video.project.json" in capsys.readouterr().err
    assert cli.main([*argv, "--force"]) == 0


def test_adopt_refuses_a_folder_that_is_not_a_project_or_a_missing_source(tmp_path, capsys):
    assert cli.main(["adopt", str(tmp_path), "--alignment", str(tmp_path / "nope.json")]) == 1
    err = capsys.readouterr().err
    assert "index.html" in err and "nope.json" in err


def test_the_rules_ask_for_a_look_at_each_new_picture_before_it_goes_in_a_layer_and_a_question_before_a_remake():
    rules = " ".join(INSTRUCTIONS.split())

    assert "Look at the new picture" in rules
    assert "remake it" in rules and "costs again, so ask" in rules
    # what the look is belongs to the profile (its house rules), so the rules name no profile's style
    assert "matches the profile's look (its house rules say what that is)" in rules
    assert "engraved" not in rules and "cross-hatching" not in rules


def test_the_rules_leave_a_profiles_own_rules_to_its_house_rules_and_teach_the_free_media_tools():
    rules = " ".join(INSTRUCTIONS.split())

    assert "project_info first" in rules and "profile's house rules" in rules
    assert "how long it shows is the profile's house rule" in rules
    assert "search_media" in rules and "fetch_media" in rules
    assert "replace_media needs no credit argument" in rules
    assert "(search_media and fetch_media do)" in rules  # the app's own finders bring no licence record


def test_new_chat_moves_the_run_file_aside_and_keeps_it(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda: False)
    runs = tmp_path / ".hyperframes" / "agent-runs.jsonl"
    runs.parent.mkdir()
    runs.write_text('{"sessionId": "b5e607a0"}\n')

    assert cli.main(["new-chat", str(tmp_path)]) == 0

    assert not runs.exists()
    (kept,) = runs.parent.glob("agent-runs.*.jsonl")
    assert kept.read_text() == '{"sessionId": "b5e607a0"}\n'
    assert str(kept) in capsys.readouterr().out


def test_new_chat_leaves_the_chat_alone_while_the_app_is_running(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda: True)
    runs = tmp_path / ".hyperframes" / "agent-runs.jsonl"
    runs.parent.mkdir()
    runs.write_text("x\n")

    assert cli.main(["new-chat", str(tmp_path)]) == 1

    assert runs.read_text() == "x\n"
    assert "Cmd+Q" in capsys.readouterr().err


def test_new_chat_of_a_project_with_no_chat_says_so(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_app_running", lambda: True)

    assert cli.main(["new-chat", str(tmp_path)]) == 0

    assert "nothing to reset" in capsys.readouterr().out


def test_the_app_is_found_by_its_bundle_process(monkeypatch):
    seen = []

    def pgrep(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", pgrep)

    assert cli._app_running() is True
    assert seen == [["pgrep", "-f", "HyperFrames.app/Contents/MacOS/"]]


def test_register_env_puts_a_spend_variable_in_the_entry_and_refuses_any_other_name():
    command = cli.register_command(python="/venv/bin/python", env=("HYPERFRAMES_MCP_PER_PROJECT_USD=5",))

    assert command[command.index("-e") + 1] == "HYPERFRAMES_MCP_PER_PROJECT_USD=5"
    assert command.index("-e") < command.index("--")
    for bad in ("PATH=/tmp", "HYPERFRAMES_MCP_PER_CALL_USD=", "HYPERFRAMES_MCP_PER_CALL_USD"):
        with pytest.raises(ValueError, match="--env takes NAME=VALUE"):
            cli.register_command(env=(bad,))


def test_the_spend_command_freezes_thaws_shows_and_clears(capsys):
    from hyperframes_mcp.motion import spend

    assert cli.main(["spend", "status"]) == 0
    assert "paid calls: allowed" in capsys.readouterr().out
    assert cli.main(["spend", "freeze"]) == 0
    try:
        assert cli.main(["spend", "status"]) == 0
        assert "frozen" in capsys.readouterr().out
    finally:
        assert cli.main(["spend", "thaw"]) == 0
    assert spend.frozen() is None
    assert cli.main(["spend", "clear", "q-nope", "--billed"]) == 1
    assert "no hold for quote q-nope" in capsys.readouterr().err


def test_a_variable_that_is_not_a_number_is_an_error_line_not_a_crash(monkeypatch, capsys):
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_CALL_USD", "lots")

    assert cli.main(["spend", "status"]) == 1
    assert "HYPERFRAMES_MCP_PER_CALL_USD" in capsys.readouterr().err
