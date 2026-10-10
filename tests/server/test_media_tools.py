"""``search_media`` and ``fetch_media`` as the app's agent calls them, through an in-memory MCP client. The four sources and the
file host are one httpx MockTransport over the archival fixtures (real captures of Wikimedia, loc.gov and the Archive, and
a response shaped like Openverse's), so no test touches a host and nothing is paid."""

from __future__ import annotations

import json

import pytest

from hyperframes_mcp.motion import manifest
from tests.motion.test_project_edit import composed, two_shots, values
from tests.server.archive_helpers import PICTURE
from tests.server.test_tools import call, images, text


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A composed project of profile "history" (a profile with no bundle, so the default search preferences)."""
    root = composed(tmp_path, two_shots())[0]
    manifest.write_manifest(root, manifest.ProjectManifest(profile="history"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def titles(result) -> list[str]:
    return [hit["title"] for hit in result.structured_content["hits"]]


@pytest.mark.asyncio
async def test_a_search_asks_the_four_archives_and_shows_the_public_domain_hits_with_what_a_person_needs(
    project, archives
):
    result = await call("search_media", {"query": "oil refinery"})

    found = result.structured_content
    assert not result.is_error
    assert found["project"] == str(project.resolve())
    assert titles(result) == ["Standard Oil Refinery Richmond CA 1913"]
    (hit,) = found["hits"]
    assert set(hit) == {"id", "title", "source", "licence", "credit", "size", "date"}
    assert hit["source"] == "Wikimedia Commons" and hit["licence"] == "Public domain"
    assert hit["size"] == "2905x700" and "West Coast Art Co." in hit["credit"]
    assert len(hit["credit"]) <= 140
    assert [line.split(":")[0] for line in found["tried"]] == [
        "loc",
        "wikimedia",
        "internet_archive",
        "openverse",
    ]
    assert "left out" in found["tried"][1]  # the CC BY and the share-alike ones
    assert found["allow_by"] is False and found["licences"] == "CC0 and public domain"
    assert "fetch_media" in found["next"] and found["retried"] == []
    # every request, to every archive, names itself
    assert {r.url.host for r in archives} == {
        "commons.wikimedia.org",
        "www.loc.gov",
        "archive.org",
        "api.openverse.org",
    }
    assert all("hyperframes-mcp" in r.headers["user-agent"] for r in archives)
    assert len(text(result)) < 8000  # a handful, not whole records: the chat's result limit is 25k tokens


@pytest.mark.asyncio
async def test_cc_by_hits_come_only_when_the_call_asks_for_them(project, archives):
    result = await call("search_media", {"query": "oil refinery", "allow_by": True})

    assert result.structured_content["allow_by"] is True
    assert set(titles(result)) == {
        "Standard Oil Refinery Richmond CA 1913",
        "Anacortes Refinery 31911",
        "Moscow Oil Refinery 02",
    }
    by = next(h for h in result.structured_content["hits"] if h["title"].startswith("Moscow"))
    assert by["licence"] == "CC-BY-4.0" and by["credit"].endswith(". CC-BY-4.0.")


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_says_what_it_tried_and_what_to_do(project, archives):
    result = await call("search_media", {"query": "zeppelin hangar"})

    found = result.structured_content
    assert not result.is_error and found["hits"] == []
    assert len(found["tried"]) == 4
    assert "nothing usable" in found["next"]


@pytest.mark.asyncio
async def test_a_clip_search_says_that_no_free_clip_source_is_wired(project, archives):
    result = await call("search_media", {"query": "vault door", "kind": "clip"})

    assert result.is_error
    assert "still pictures only" in text(result) and "quote_clip" in text(result)
    assert archives == []  # nothing was asked


@pytest.mark.asyncio
async def test_an_empty_query_and_a_limit_out_of_range_are_errors_in_words(project, archives):
    assert "what the picture shows" in text(await call("search_media", {"query": " "}))
    refused = await call("search_media", {"query": "oil refinery", "limit": 50})
    assert refused.is_error and "limit is from 1 to 10" in text(refused)


@pytest.mark.asyncio
async def test_a_fetched_picture_lands_in_found_with_its_record_and_a_preview_to_look_at(project, archives):
    found = await call("search_media", {"query": "oil refinery"})
    hit = found.structured_content["hits"][0]

    result = await call("fetch_media", {"id": hit["id"], "name": "refinery"})

    made = result.structured_content
    assert not result.is_error
    assert made["path"] == "assets/media/found/refinery.jpg"
    assert (project / made["path"]).read_bytes() == PICTURE
    assert made["credit"] == hit["credit"] and made["licence"] == "Public domain"
    assert made["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    assert made["verify_at_source"] is True and "confirm it" in made["next"]
    (preview,) = images(result)
    assert preview.format == "JPEG" and preview.width <= 640
    assert "replace_media" in made["next"] and '"path"' in text(result)
    record = json.loads((project / (made["path"] + ".json")).read_text())
    assert record["ai"] is False and record["credit"] == hit["credit"]


@pytest.mark.asyncio
async def test_replace_media_takes_the_credit_of_a_fetched_picture_from_its_record_with_no_credit_argument(
    project, archives
):
    found = await call("search_media", {"query": "oil refinery"})
    hit = found.structured_content["hits"][0]
    fetched = (await call("fetch_media", {"id": hit["id"], "name": "refinery"})).structured_content

    result = await call("replace_media", {"layer": "l-p1-1", "file": fetched["path"]})

    done = result.structured_content
    assert not result.is_error
    assert done["credit"] == hit["credit"]
    assert done["new"] == "assets/media/edits/refinery.jpg"
    assert values(project, "l-p1-2")["text"] == hit["credit"]  # the credit line on screen with the picture
    placed = (await call("layer_table", {"kinds": ["photo-shot"]})).structured_content["layers"][0]
    assert placed["source"] == "archival" and placed["file"] == done["new"]
    # the record travelled with the file, so the layer table still knows who made it
    assert (project / (done["new"] + ".json")).is_file()


@pytest.mark.asyncio
async def test_a_hit_that_was_never_found_and_a_name_that_leaves_the_folder_are_errors_in_words(project, archives):
    found = await call("search_media", {"query": "oil refinery"})
    hit = found.structured_content["hits"][0]["id"]

    never = await call("fetch_media", {"id": "deadbeef", "name": "x"})
    outside = await call("fetch_media", {"id": hit, "name": "../../escape"})

    assert never.is_error and "search_media first" in text(never)
    assert outside.is_error and "letters, digits" in text(outside)
    assert not (project / "assets" / "media" / "found").exists()


@pytest.mark.asyncio
async def test_a_second_fetch_under_the_same_name_is_refused_and_the_first_file_stays(project, archives):
    hit = (await call("search_media", {"query": "oil refinery"})).structured_content["hits"][0]["id"]
    await call("fetch_media", {"id": hit, "name": "refinery"})

    again = await call("fetch_media", {"id": hit, "name": "refinery"})

    assert again.is_error and "already a file" in text(again)
    assert (project / "assets" / "media" / "found" / "refinery.jpg").read_bytes() == PICTURE


@pytest.mark.asyncio
async def test_a_search_found_with_fewer_words_names_the_wording_that_found_it(project, monkeypatch):
    from hyperframes_mcp.motion import media

    async def found(*args, **kwargs):
        return {
            "query": "Marburg vault wall hole 1945 gold",
            "hits": [{"id": "ab12cd34", "title": "Marburg vault"}],
            "tried": [],
            "retried": ["Marburg 1945", "Marburg vault"],
        }

    monkeypatch.setattr(media, "search", found)

    result = await call("search_media", {"query": "Marburg vault wall hole 1945 gold"})

    assert "found with fewer words (Marburg vault)" in result.structured_content["next"]
    assert "Marburg 1945" not in result.structured_content["next"]
