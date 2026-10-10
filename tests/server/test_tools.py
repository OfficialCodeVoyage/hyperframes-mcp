"""The Phase 1 tools as the HyperFrames app's agent calls them, through an in-memory MCP client: what a project is,
what is on screen when and what is said over it, quote then confirm under the limits, and replacing a picture. The
paid calls are faked (``tests.motion.test_spend``'s ``paid``), so no test spends money.
"""

from __future__ import annotations

import base64
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from mcp import Client
from PIL import Image

from hyperframes_mcp.motion import manifest, project_edit, spend
from hyperframes_mcp.server import version
from hyperframes_mcp.server.bridge import AppBridge
from hyperframes_mcp.server.server import build_server
from tests.motion.spend_helpers import earlier_spend
from tests.motion.test_project_edit import composed, two_shots

FAKE_KEY = "sk-test-never-shown"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A composed project of profile "history", topic 1, with the narration's words in its manifest."""
    root = composed(tmp_path, two_shots())[0]
    words = tmp_path / "words.json"
    words.write_text(
        json.dumps(
            [
                {"word": "It", "start": 0.2, "end": 0.4},
                {"word": "is", "start": 0.4, "end": 0.6},
                {"word": "April.", "start": 0.6, "end": 1.0},
                {"word": "Gold.", "start": 30.0, "end": 30.5},
            ]
        )
    )
    manifest.write_manifest(
        root,
        manifest.ProjectManifest(profile="history", topic_id=1, alignment=words),
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    return root


async def call(name: str, arguments: dict | None = None, bridge: AppBridge | None = None):
    """One tool call; the bridge goes to a fake app, or by default to no app at all (never the real one)."""
    bridge = bridge or AppBridge(Path("/nonexistent/hyperframes-app-support"))
    async with Client(build_server(bridge)) as client:
        return await client.call_tool(name, arguments or {})


def text(result) -> str:
    return "".join(getattr(part, "text", "") for part in result.content)


def images(result) -> list[Image.Image]:
    """The pictures among a result's content blocks, decoded."""
    return [Image.open(io.BytesIO(base64.b64decode(part.data))) for part in result.content if part.type == "image"]


@pytest.mark.asyncio
async def test_project_info_names_the_session_folders_project_its_profile_sources_and_limits(
    project,
):
    result = await call("project_info")

    info = result.structured_content
    assert info["project"] == str(project.resolve())
    assert (info["profile"], info["topic_id"]) == ("history", 1)
    assert info["sources"]["alignment"]["exists"] is True
    assert info["spend"]["limits"]["per_call_usd"] == 1.0
    assert '"profile": "history"' in text(result)  # the content carries it too


@pytest.mark.asyncio
async def test_layer_table_gives_each_layer_its_credit_source_and_the_sentence_spoken_over_it(
    project,
):
    result = await call("layer_table", {"kinds": ["photo-shot"]})

    layers = result.structured_content["layers"]
    assert [layer["kind"] for layer in layers] == ["photo-shot"]
    photo = layers[0]
    assert photo["credits"] and photo["source"] == "archival"
    assert photo["narration"] == "It is April."


@pytest.mark.asyncio
async def test_project_info_names_the_version_of_the_server_it_runs_on(project):
    shown = (await call("project_info")).structured_content["version"]

    assert shown["pid"] == os.getpid()
    assert shown["git_sha"] == version.STARTED_SHA
    assert shown["started_at"] == version.STARTED_AT


@pytest.mark.asyncio
async def test_layer_table_leaves_the_credit_line_rows_out_unless_asked_and_gives_every_text_whole(
    project,
):
    long_credit = (
        "Photo: U.S. Mint, San Francisco, c.1935, National Archives and Records Administration (public domain)"
    )
    page = project / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace("US Army Signal Corps, NARA 111-SC-204515", long_credit),
        encoding="utf-8",
    )

    layers = (await call("layer_table")).structured_content["layers"]
    asked = (await call("layer_table", {"kinds": ["credit-line", "photo-shot"]})).structured_content["layers"]

    assert [layer["kind"] for layer in layers] == ["photo-shot", "video-shot"]
    # the credit is still there, whole, under the picture it belongs to
    assert layers[0]["credits"][0]["text"] == long_credit
    assert [layer["kind"] for layer in asked] == [
        "photo-shot",
        "credit-line",
        "credit-line",
    ]
    assert asked[1]["text"] == long_credit


@pytest.mark.asyncio
async def test_layer_table_names_the_kinds_there_are_when_one_is_not_a_kind_of_layer(
    project,
):
    refused = await call("layer_table", {"kinds": ["audio", "photo-shot"]})
    fine = await call("layer_table", {"kinds": ["photo-shot"]})

    assert refused.is_error
    assert "no layer kind 'audio'" in text(refused) and "counter" in text(refused)
    assert "narration" in text(refused)  # the audio is not a layer: narration gives the words
    assert not fine.is_error


@pytest.mark.asyncio
async def test_layer_table_accepts_a_kind_the_page_holds_that_the_library_does_not_list(
    project,
):
    # the page is the truth: a layer the app or a hand edit put there under another composition id is listed, so it
    # can be asked for by that kind
    page = (project / "index.html").read_text(encoding="utf-8")
    tag = project_edit.mounts(page)["l-m1-1"].tag
    (project / "index.html").write_text(
        project_edit.set_attributes(page, tag, {"data-composition-id": "my-intro"}),
        encoding="utf-8",
    )

    asked = await call("layer_table", {"kinds": ["my-intro"]})
    refused = await call("layer_table", {"kinds": ["audio"]})

    assert not asked.is_error
    assert [row["id"] for row in asked.structured_content["layers"]] == ["l-m1-1"]
    assert "my-intro" in text(refused) and "counter" in text(refused)


@pytest.mark.asyncio
async def test_the_clip_quote_tool_says_which_lengths_the_model_makes(project):
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert "4, 6 or 8" in tools["quote_clip"].description


@pytest.mark.asyncio
async def test_a_broken_clip_settings_file_leaves_the_server_and_its_tools_standing(project, monkeypatch):
    def broken():
        raise yaml.YAMLError("unbalanced quote")

    monkeypatch.setattr("hyperframes_mcp.motion.generate.allowed_clip_seconds", broken)

    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert "a length the model makes" in tools["quote_clip"].description
    assert "layer_table" in tools


@pytest.mark.asyncio
async def test_narration_gives_each_word_with_its_time_in_a_stretch(project):
    result = (await call("narration", {"from_s": 0, "to_s": 2})).structured_content

    assert [w["word"] for w in result["words"]] == ["It", "is", "April."]
    assert result["words"][2] == {"word": "April.", "start": 0.6, "end": 1.0}
    assert result["text"] == "It is April." and result["count"] == 3
    assert "matches" not in result


@pytest.mark.asyncio
async def test_narration_finds_where_a_phrase_is_said_and_only_in_the_stretch_asked(
    project,
):
    found = (await call("narration", {"phrase": "gold"})).structured_content
    elsewhere = (await call("narration", {"phrase": "gold", "to_s": 10})).structured_content

    assert [(m["text"], m["start"], m["end"]) for m in found["matches"]] == [("Gold.", 30.0, 30.5)]
    assert elsewhere["matches"] == []
    assert "try the other form" in found["note"]


@pytest.mark.asyncio
async def test_a_blank_phrase_and_a_moved_alignment_file_are_told_in_words(project, tmp_path):
    blank = await call("narration", {"phrase": " ,. "})
    assert blank.is_error and "no words to look for" in text(blank)

    (tmp_path / "words.json").unlink()
    moved = await call("narration", {"from_s": 0, "to_s": 2})
    assert moved.is_error and "words.json" in text(moved)
    table = await call("layer_table", {"from_s": 0, "to_s": 2})
    assert table.is_error and "words.json" in text(table)


@pytest.mark.asyncio
async def test_a_stretch_that_ends_before_it_starts_is_refused_and_an_empty_alignment_says_so(project, tmp_path):
    backwards = await call("narration", {"from_s": 5, "to_s": 1})
    assert backwards.is_error and "from_s (5" in text(backwards)

    (tmp_path / "words.json").write_text("[]")
    empty = (await call("narration")).structured_content
    assert empty["count"] == 0 and "holds no words" in empty["note"]


@pytest.mark.asyncio
async def test_a_long_stretch_of_narration_is_cut_and_says_so(project, monkeypatch):
    from hyperframes_mcp.server import context_tools

    monkeypatch.setattr(context_tools, "MAX_WORDS", 2)

    result = (await call("narration")).structured_content

    assert result["count"] == 4 and len(result["words"]) == 2
    assert result["truncated"] is True and "shorter stretch" in result["note"]


@pytest.mark.asyncio
async def test_narration_of_a_project_without_an_alignment_says_so(project):
    manifest.write_manifest(project, manifest.ProjectManifest(profile="history"))

    refused = await call("narration")

    assert refused.is_error and "no narration alignment" in text(refused)


@pytest.mark.asyncio
async def test_a_picture_is_quoted_with_its_prompt_then_confirmed_once_and_recorded(project, paid):

    quoted = (
        await call(
            "quote_picture",
            {"request": "a vault door by lamplight", "name": "vault-door"},
        )
    ).structured_content
    assert quoted["out"] == "assets/media/edits/vault-door.jpg"
    assert "a vault door by lamplight" in quoted["prompt"]
    assert paid == []

    made = (await call("confirm_spend", {"quote_id": quoted["quote_id"]})).structured_content
    again = (await call("confirm_spend", {"quote_id": quoted["quote_id"]})).structured_content

    assert made["path"] == "assets/media/edits/vault-door.jpg"
    assert made["recorded"]["where"] == "spend_log"
    assert again["replayed"] is True
    assert [kind for kind, *_ in paid] == ["picture"]


@pytest.mark.asyncio
async def test_a_confirmed_picture_comes_with_a_small_preview_to_look_at_and_a_replay_gives_it_again(project, paid):
    quoted = (await call("quote_picture", {"request": "a cage", "name": "cage"})).structured_content

    first = await call("confirm_spend", {"quote_id": quoted["quote_id"]})
    again = await call("confirm_spend", {"quote_id": quoted["quote_id"]})

    for result in (first, again):
        (preview,) = images(result)
        assert preview.format == "JPEG" and preview.width <= 640
        assert result.structured_content["path"] == "assets/media/edits/cage.jpg"
        assert "house rules" in result.structured_content["next"]
        assert '"quote_id"' in text(result)  # the text block still carries the answer
    assert "replayed" not in first.structured_content
    assert again.structured_content["replayed"] is True
    assert [kind for kind, *_ in paid] == ["picture"]
    # after the file was removed there is nothing to hand back: the answer says the call was made and billed, and that
    # a new file needs a new quote (it is never made again)
    (project / "assets" / "media" / "edits" / "cage.jpg").unlink()
    gone = await call("confirm_spend", {"quote_id": quoted["quote_id"]})
    assert gone.is_error and "already made and billed" in text(gone)
    assert [kind for kind, *_ in paid] == ["picture"]


@pytest.mark.asyncio
async def test_a_confirmed_picture_that_cannot_be_read_is_still_reported_without_a_preview(project, paid, monkeypatch):
    quoted = (await call("quote_picture", {"request": "a cage", "name": "cage"})).structured_content
    real_confirm = spend.confirm

    async def confirm_then_damage(root, quote_id):
        made = await real_confirm(root, quote_id)
        (root / made["path"]).write_bytes(b"not a picture")
        return made

    monkeypatch.setattr(spend, "confirm", confirm_then_damage)

    result = await call("confirm_spend", {"quote_id": quoted["quote_id"]})

    assert not result.is_error and images(result) == []
    assert result.structured_content["cost_usd"] > 0  # the paid call is reported


@pytest.mark.asyncio
async def test_a_confirmed_clip_comes_without_a_preview(project, paid):
    Image.new("RGB", (64, 36), "black").save(project / "assets" / "still.png")
    quoted = (
        await call(
            "quote_clip",
            {
                "still": "assets/still.png",
                "motion": "push in",
                "name": "c",
                "seconds": 4,
            },
        )
    ).structured_content

    result = await call("confirm_spend", {"quote_id": quoted["quote_id"]})

    assert not result.is_error and images(result) == []
    assert result.structured_content["path"] == "assets/media/edits/c.mp4"
    assert "next" not in result.structured_content
    assert [kind for kind, *_ in paid] == ["clip"]


@pytest.mark.asyncio
async def test_a_confirm_over_the_videos_limit_is_refused_in_words_and_nothing_is_paid(project, paid):
    quoted = (await call("quote_picture", {"request": "a vault", "name": "vault"})).structured_content
    earlier_spend(project, 9.99, at=datetime.now(UTC))

    result = await call("confirm_spend", {"quote_id": quoted["quote_id"]})

    assert result.is_error
    assert "has spent $9.99 of $10.00" in text(result)
    assert paid == []


@pytest.mark.asyncio
async def test_a_clip_quote_of_a_length_the_model_cannot_make_is_a_clear_error(project):
    (project / "assets" / "still.png").write_bytes(b"png")

    result = await call(
        "quote_clip",
        {"still": "assets/still.png", "motion": "push in", "name": "c", "seconds": 5},
    )

    assert result.is_error
    assert "4" in text(result) and "6" in text(result) and "8" in text(result)


@pytest.mark.asyncio
async def test_a_new_file_name_cannot_leave_the_edits_folder(project):
    result = await call("quote_picture", {"request": "x", "name": "../../escape"})

    assert result.is_error and "letters, digits" in text(result)


@pytest.mark.asyncio
async def test_replace_media_takes_a_file_only_from_the_project(project, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"a picture from anywhere")
    refused = await call("replace_media", {"layer": "l-p1-1", "file": str(outside), "credit": "x"})
    assert refused.is_error and "outside the project" in text(refused)


@pytest.mark.asyncio
async def test_replace_media_does_not_trust_the_manifests_assets_folder(project, tmp_path):
    # the agent can rewrite the manifest, so an assets folder it names opens nothing
    anywhere = tmp_path / "anywhere"
    anywhere.mkdir()
    (anywhere / "secret.png").write_bytes(b"not the project's")
    manifest.write_manifest(project, manifest.read_manifest(project).model_copy(update={"assets": anywhere}))

    refused = await call(
        "replace_media",
        {"layer": "l-p1-1", "file": str(anywhere / "secret.png"), "credit": "x"},
    )

    assert refused.is_error and "outside the project" in text(refused)
    assert not (project / "assets" / "media" / "edits" / "secret.png").exists()


@pytest.mark.asyncio
async def test_replace_media_puts_a_credited_picture_in_a_layer(project):
    new = project / "incoming" / "new.png"
    new.parent.mkdir()
    new.write_bytes(b"a new picture")

    result = await call(
        "replace_media",
        {"layer": "l-p1-1", "file": str(new), "credit": "National Archives"},
    )

    done = result.structured_content
    assert done["new"] == "assets/media/edits/new.png"
    assert done["credit"] == "National Archives"


@pytest.mark.asyncio
async def test_no_tool_output_shows_a_key(project):
    for name, arguments in (
        ("ping", {}),
        ("project_info", {}),
        ("style_info", {}),
        ("quote_picture", {"request": "a vault", "name": "v"}),
    ):
        assert FAKE_KEY not in str(await call(name, arguments))


@pytest.mark.asyncio
async def test_a_call_that_may_have_been_billed_is_an_error_that_tells_the_agent_to_stop_and_the_state_shows_the_hold(
    project, paid, monkeypatch
):
    from hyperframes_mcp.motion import generate

    quoted = (await call("quote_picture", {"request": "a vault door", "name": "door"})).structured_content

    async def drops(*args, **kwargs):
        raise RuntimeError("the connection dropped")

    monkeypatch.setattr(generate, "make_picture", drops)
    failed = await call("confirm_spend", {"quote_id": quoted["quote_id"]})
    again = await call("confirm_spend", {"quote_id": quoted["quote_id"]})
    status = (await call("spend_status", {})).structured_content["video"]

    assert failed.is_error and "may have been billed" in text(failed) and "spend clear" in text(failed)
    assert again.is_error and "spend clear" in text(again)
    assert [hold["quote_id"] for hold in status["uncertain"]] == [quoted["quote_id"]]
    assert status["spent_usd"] == quoted["price_usd"]
