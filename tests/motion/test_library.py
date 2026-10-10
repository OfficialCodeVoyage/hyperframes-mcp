"""Hygiene of everything under ``assets/motion``.

Every component lints clean and has a licence, no component is missing from the registry, and the notes that
record where third-party files came from are in step with the files.
"""

from __future__ import annotations

import re

from hyperframes_mcp.motion import lint, registry, tokens
from tests.conftest import REPO

MOTION = tokens.MOTION_DIR


def test_every_registered_item_lints_clean_and_has_a_licence():
    assert registry.list_items(), "the registry is empty"
    for item in registry.list_items():
        assert item.license, item.name
        for entry in item.files:
            if entry.path.endswith(".html"):
                assert lint.lint_file(MOTION / entry.path) == [], entry.path


def test_the_whole_tree_lints_clean():
    assert lint.lint_tree(MOTION) == []


def test_every_block_takes_durationSeconds_first_and_its_default_is_the_root_duration():
    """The composer fits a graphic to a slot through durationSeconds, so every block declares it."""
    for item in registry.list_items():
        if item.type != "hyperframes:block":
            continue
        first = item.variables[0]
        assert first["id"] == "durationSeconds", item.name
        assert first["type"] == "number" and first["min"] > 0, item.name

        html = (MOTION / item.files[0].path).read_text(encoding="utf-8")
        root = float(re.search(r'data-duration="([\d.]+)"', html).group(1))
        assert root == first["default"] == item.duration, item.name


def test_no_html_file_is_missing_from_the_registry():
    registered = {entry.path for item in registry.list_items() for entry in item.files}
    on_disk = {p.relative_to(MOTION).as_posix() for p in MOTION.rglob("*.html")}

    assert on_disk == {p for p in registered if p.endswith(".html")}


def test_a_vendored_item_names_its_source_in_third_party():
    third_party = (MOTION / "THIRD_PARTY.md").read_text(encoding="utf-8")
    for item in registry.list_items():
        if item.source is not None:
            assert item.name in third_party, f"{item.name} is vendored but not in THIRD_PARTY.md"
            assert item.source.sha in third_party, item.name


def test_third_party_records_gsap_and_every_font():
    third_party = (MOTION / "THIRD_PARTY.md").read_text(encoding="utf-8")

    assert "GSAP Standard License" in third_party
    assert "not an OSI" in third_party
    for font in tokens.default_tokens().fonts.values():
        assert font.family in third_party


def test_fonts_md_lists_every_font_file_with_its_exact_size():
    notes = (MOTION / "fonts" / "FONTS.md").read_text(encoding="utf-8")
    files = sorted(p for p in (MOTION / "fonts").iterdir() if p.suffix in (".ttf", ".txt"))

    assert files
    for path in files:
        row = next(
            (line for line in notes.splitlines() if line.startswith("|") and f"`{path.name}`" in line),
            None,
        )
        assert row is not None, f"{path.name} is not in FONTS.md"
        assert re.search(rf"\b{path.stat().st_size}\b", row), f"{path.name}: wrong byte size in FONTS.md"


def test_the_ofl_text_is_the_licence_of_every_font():
    for font in tokens.default_tokens().fonts.values():
        text = (tokens.FONTS_DIR / font.licence_file).read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE Version 1.1" in text, font.licence_file


def test_gsap_is_not_shipped_with_the_package():
    assert not list(MOTION.rglob("gsap*.js")), (
        "GSAP has its own licence: setup installs it, the package never carries it"
    )


def test_the_motion_doc_names_exactly_the_token_colours():
    doc = (REPO / "docs" / "motion-library.md").read_text(encoding="utf-8")
    token_colours = {c.upper() for c in tokens.default_tokens().colors.values()}
    named = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", doc)}

    assert named == token_colours


def test_every_lint_code_is_documented():
    doc = (REPO / "docs" / "motion-library.md").read_text(encoding="utf-8")
    missing = [code for code in lint.CODES if f"`{code}`" not in doc]

    assert not missing, f"motion-library.md does not describe {missing}"
