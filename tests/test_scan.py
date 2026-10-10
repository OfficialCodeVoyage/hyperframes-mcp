"""``scripts/scan_tree.py`` finds what it should and does not echo it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scan_tree", ROOT / "scripts" / "scan_tree.py")
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def kinds(text: str, terms=()) -> set[str]:
    return {kind for _, kind in scan.scan_text(text, list(terms))}


def test_keys_and_tokens_are_found():
    assert "api key (sk-)" in kinds("OPENAI_API_KEY=sk-" + "a" * 30)
    assert "github token" in kinds("token ghp_" + "A1" * 20)
    assert "fal key" in kinds("FAL_KEY=" + "0123abcd-0123-0123-0123-0123456789ab:" + "f" * 32)
    assert "private key" in kinds("-----BEGIN " + "PRIVATE KEY-----")
    assert "secret assignment" in kinds('api_key = "abcdefghijklmnopqrstuvwxyz0123"')  # scan:ignore


def test_personal_paths_and_emails_are_found_but_placeholders_are_not():
    assert "personal path" in kinds("see /Users/someperson/project/file")  # scan:ignore
    assert "e-mail address" in kinds("write to jane.doe@company.io")  # scan:ignore
    assert kinds("see /Users/you/project and /home/user/x and ops@example.org") == set()


def test_ordinary_text_is_clean():
    assert kinds("OPENAI_API_KEY is read from the environment; FAL_KEY too.") == set()


def test_a_refused_word_is_found_case_insensitively(tmp_path, monkeypatch):
    words = tmp_path / "terms.txt"
    words.write_text("Secret Project\n", encoding="utf-8")
    monkeypatch.setenv("SCAN_TERMS_FILE", str(words))

    assert "refused word" in kinds("about the SECRET PROJECT today", scan.extra_terms())


def test_a_line_marked_scan_ignore_is_skipped():
    assert kinds('TOKEN = "abcdefghijklmnopqrstuvwxyz0123456789"  # scan:ignore') == set()
