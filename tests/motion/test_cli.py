"""``python -m hyperframes_mcp.motion``: every subcommand once, offline, with the exit codes the docs promise."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from hyperframes_mcp.motion import __main__ as cli
from hyperframes_mcp.motion import registry, tokens

REPO = Path(__file__).resolve().parents[2]
BAD = """<!doctype html><html><body>
<div data-composition-id="x" data-width="1920" data-height="1080" data-start="0" data-duration="2"
 data-composition-variables='[]'></div>
<script>var tl = gsap.timeline(); var r = Math.random();</script></body></html>
"""


def test_list_names_the_reference_component(capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "credit-line" in out
    assert "1920x1080" in out


def test_show_prints_files_variables_and_source(capsys):
    assert cli.main(["show", "credit-line"]) == 0
    out = capsys.readouterr().out
    assert "blocks/credit-line.html -> compositions/credit-line.html" in out
    assert '"id": "durationSeconds"' in out
    assert "source: ours" in out


def test_show_an_unknown_item_exits_2_and_says_what_exists(capsys):
    assert cli.main(["show", "nope"]) == 2
    assert "credit-line" in capsys.readouterr().err


def test_lint_of_the_library_is_clean(capsys):
    assert cli.main(["lint"]) == 0
    assert "clean" in capsys.readouterr().out


def test_lint_exits_1_on_a_problem_and_prints_it(tmp_path, capsys):
    bad = tmp_path / "bad.html"
    bad.write_text(BAD, encoding="utf-8")

    assert cli.main(["lint", str(bad)]) == 1

    out = capsys.readouterr().out
    assert "NOT_PAUSED" in out
    assert "RANDOM_OR_CLOCK" in out
    assert "NO_TIMELINE_REGISTRATION" in out
    assert out.strip().splitlines()[-1] == "3 problem(s) in 1 file(s)"


def test_lint_takes_folders_and_several_paths(tmp_path, capsys):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "bad.html").write_text(BAD, encoding="utf-8")
    good = registry.MOTION_DIR / "blocks" / "credit-line.html"

    assert cli.main(["lint", str(good), str(tmp_path / "a")]) == 1
    assert "in 2 file(s)" in capsys.readouterr().out


def test_lint_of_a_missing_path_exits_2(tmp_path, capsys):
    assert cli.main(["lint", str(tmp_path / "missing.html")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_install_writes_the_project_and_reports_kept_files(tmp_path, capsys):
    project = tmp_path / "project"

    assert cli.main(["install", "credit-line", "--dir", str(project)]) == 0
    out = capsys.readouterr().out
    assert "wrote compositions/credit-line.html" in out
    assert (project / "tokens.css").is_file()

    (project / "compositions" / "credit-line.html").write_text("mine", encoding="utf-8")
    assert cli.main(["install", "credit-line", "--dir", str(project)]) == 0
    captured = capsys.readouterr()
    assert "kept compositions/credit-line.html" in captured.err
    assert "0 written, 1 kept" in captured.out

    assert cli.main(["install", "credit-line", "--dir", str(project), "--force"]) == 0
    assert "wrote compositions/credit-line.html" in capsys.readouterr().out


def test_install_of_an_unknown_item_exits_2(tmp_path, capsys):
    assert cli.main(["install", "nope", "--dir", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err


def test_tokens_css_prints_what_an_install_writes(capsys):
    assert cli.main(["tokens-css"]) == 0
    assert capsys.readouterr().out == tokens.stylesheet()


def test_it_runs_as_a_module():
    done = subprocess.run(
        [sys.executable, "-m", "hyperframes_mcp.motion", "list"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "credit-line" in done.stdout
