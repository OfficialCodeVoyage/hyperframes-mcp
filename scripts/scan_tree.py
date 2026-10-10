"""Look for secrets, personal paths and e-mail addresses in the files of this repository (and, with --history, in every
commit), so none is published by accident.

    python scripts/scan_tree.py                   # the files git tracks (or every file, outside a git checkout)
    python scripts/scan_tree.py --history         # also every line ever added, on every branch
    SCAN_TERMS_FILE=terms.txt python scripts/scan_tree.py   # extra words to refuse, one per line (keep that file out of the repo)

A line carrying the marker ``scan:ignore`` is skipped (a fake token in a test). Exit 0 when nothing is found, 1 otherwise. A match is printed as ``path:line: kind`` and never with the matched text.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 2_000_000

PATTERNS: dict[str, re.Pattern[str]] = {
    "api key (sk-)": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "github token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "context7 key": re.compile(r"\bctx7sk-[A-Za-z0-9-]{10,}"),
    "fal key": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32}\b"),
    "private key": re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
    "secret assignment": re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|token|password)\b["']?\s*[:=]\s*["'][A-Za-z0-9_\-/+=]{24,}["']"""
    ),
    "personal path": re.compile(r"(?:/Users|/home)/(?!(?:me|you|user|name|example|runner|test|someone)\b)[A-Za-z0-9._-]+/"),
    "e-mail address": re.compile(
        r"\b[A-Za-z0-9._%+-]+@(?!(?:example\.(?:com|org|net)|users\.noreply\.github\.com|noreply\.github\.com)\b)"
        r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
    ),
}


def extra_terms() -> list[re.Pattern[str]]:
    """Words the person refuses to publish, from the file ``SCAN_TERMS_FILE`` names (one per line, case-insensitive)."""
    path = os.environ.get("SCAN_TERMS_FILE")
    if not path:
        return []
    words = [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return [re.compile(re.escape(word), re.IGNORECASE) for word in words]


def scan_text(text: str, terms: list[re.Pattern[str]]) -> list[tuple[int, str]]:
    """(line number, kind) of every match in ``text``."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if "scan:ignore" in line:  # a line that holds a fake secret on purpose, in a test
            continue
        if len(line) > 5000:  # minified data, not prose
            line = line[:5000]
        for kind, pattern in PATTERNS.items():
            if pattern.search(line):
                found.append((number, kind))
        for term in terms:
            if term.search(line):
                found.append((number, "refused word"))
    return found


def files() -> list[Path]:
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True,
        )  # fmt: skip
        return [ROOT / name for name in done.stdout.decode().split("\0") if name]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", ".venv", "node_modules", "__pycache__"}
        return [p for p in ROOT.rglob("*") if p.is_file() and not skip & set(p.relative_to(ROOT).parts)]


def scan_files(terms: list[re.Pattern[str]]) -> list[str]:
    problems: list[str] = []
    for path in files():
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT)
        problems += [f"{rel}:{number}: {kind}" for number, kind in scan_text(text, terms)]
    return problems


def scan_history(terms: list[re.Pattern[str]]) -> list[str]:
    """Every added line of every commit on every branch, and every commit author and message."""
    log = subprocess.run(
        ["git", "-C", str(ROOT), "log", "--all", "-p", "--no-color", "--format=commit %H%nauthor %an <%ae>%nmessage %B"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    problems: list[str] = []
    commit = "?"
    for line in log.stdout.splitlines():
        if line.startswith("commit "):
            commit = line.split()[1][:10]
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith(("+", "author ", "message ")):
            for _, kind in scan_text(line, terms):
                problems.append(f"history {commit}: {kind}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--history", action="store_true", help="also scan every commit")
    args = parser.parse_args()
    terms = extra_terms()
    problems = scan_files(terms) + (scan_history(terms) if args.history else [])
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s)" if problems else "clean")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
