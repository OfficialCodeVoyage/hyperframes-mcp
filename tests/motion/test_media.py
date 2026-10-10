"""Free pictures for an edit: search the free archives under the licence policy, keep the hits, fetch one into the project with
its licence record. The providers and the network are fakes (``tests.archival.helpers.FakeProvider``, an httpx
MockTransport), so nothing here touches a host."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime

import httpx
import pytest

from hyperframes_mcp.archival.base import ArchivalProviderError
from hyperframes_mcp.motion import edit_config, media
from hyperframes_mcp.motion.media import MediaError
from tests.archival.helpers import (
    BY,
    CC0,
    PD,
    RESTRICTED,
    SA,
    UNKNOWN,
    FakeProvider,
    make_candidate,
)
from tests.image_helpers import image_bytes

ALL = tuple(edit_config.SOURCES)


def pd(provider_id="1", **kwargs):
    """A public-domain candidate that matches the query "vault door" and is wide enough."""
    kwargs.setdefault("title", "Vault door of the bank")
    kwargs.setdefault("size", (1600, 1000))
    kwargs.setdefault("licence_id", None)  # a public-domain item states none
    return make_candidate(PD, provider_id=provider_id, **kwargs)


async def search(project, *candidates_by_source, query="vault door", **kwargs):
    """``media.search`` over fake providers named in the order given: ``("loc", [candidates])``."""
    providers = {name: FakeProvider(name, found) for name, found in candidates_by_source}
    kwargs.setdefault("sources", [name for name, _ in candidates_by_source])
    kwargs.setdefault("allow_by", False)
    return await media.search(project, query, providers=providers, **kwargs)


def ids(found) -> list[str]:
    return [hit["title"] for hit in found["hits"]]


@pytest.mark.asyncio
async def test_cc0_public_domain_and_the_public_domain_mark_are_found_by_default_and_everything_else_is_left_out(
    tmp_path,
):
    found = await search(
        tmp_path,
        (
            "loc",
            [
                pd("1", title="Vault door, public domain"),
                make_candidate(CC0, provider_id="2", title="Vault door, CC0", size=(1600, 1000)),
                make_candidate(BY, provider_id="3", title="Vault door, CC BY", size=(1600, 1000)),
                make_candidate(
                    SA,
                    provider_id="4",
                    title="Vault door, share-alike",
                    size=(1600, 1000),
                ),
                make_candidate(
                    RESTRICTED,
                    provider_id="5",
                    title="Vault door, non-commercial",
                    size=(1600, 1000),
                ),
                make_candidate(
                    UNKNOWN,
                    provider_id="6",
                    title="Vault door, unknown",
                    size=(1600, 1000),
                ),
            ],
        ),
    )

    assert ids(found) == ["Vault door, public domain", "Vault door, CC0"]
    assert found["licences"] == "CC0 and public domain"
    assert found["allow_by"] is False


@pytest.mark.asyncio
async def test_cc_by_is_found_only_when_asked_and_a_cc_by_picture_with_nobody_to_credit_never_is(
    tmp_path,
):
    by = make_candidate(BY, provider_id="3", title="Vault door, CC BY", size=(1600, 1000))
    nameless = make_candidate(
        BY, provider_id="4", title="Vault door, nobody", size=(1600, 1000), creator=None,
        needs_review=True, review_reasons=("no creator or attribution text to credit",),
    )  # fmt: skip

    asked = await search(tmp_path, ("loc", [by, nameless]), allow_by=True)
    default = await search(tmp_path, ("loc", [by, nameless]))

    assert ids(asked) == ["Vault door, CC BY"]
    assert asked["licences"] == "CC0, public domain and CC BY"
    assert asked["hits"][0]["licence"] == "CC-BY-4.0"
    assert default["hits"] == []
    assert "2 CC BY not asked for" in default["tried"][0]


@pytest.mark.asyncio
async def test_a_hit_has_a_title_a_source_a_licence_a_credit_a_size_a_date_and_a_short_id(
    tmp_path,
):
    found = await search(
        tmp_path,
        ("loc", [pd("77", creator="Jack Delano", date="1940", size=(2400, 1600))]),
    )

    (hit,) = found["hits"]
    assert hit == {
        "id": hit["id"],
        "title": "Vault door of the bank",
        "source": "Wikimedia Commons",  # the candidate's own provider, as make_candidate names it
        "licence": "Public domain",
        "credit": "Jack Delano, Wikimedia Commons. Public domain.",
        "size": "2400x1600",
        "date": "1940",
    }
    assert len(hit["id"]) == 8 and int(hit["id"], 16) >= 0


@pytest.mark.asyncio
async def test_the_id_is_the_same_for_the_same_picture_and_differs_between_pictures(
    tmp_path,
):
    one = await search(tmp_path, ("loc", [pd("1"), pd("2", title="Vault door two")]))
    again = await search(tmp_path, ("loc", [pd("1"), pd("2", title="Vault door two")]))

    assert [h["id"] for h in one["hits"]] == [h["id"] for h in again["hits"]]
    assert len({h["id"] for h in one["hits"]}) == 2


@pytest.mark.asyncio
async def test_the_on_screen_credit_fits_the_credit_line_block_and_keeps_who_made_it_and_the_licence():
    long_title = "The vault door of the old Treasury building " * 6
    candidate = pd(
        title=long_title,
        creator="Dorothea Lange",
        licence_id="CC-BY-4.0",
    ).model_copy(update={"rights_status": BY})

    credit = media.screen_credit(candidate)

    assert len(credit) <= media.CREDIT_CHARS
    assert credit.endswith(", Wikimedia Commons. CC-BY-4.0.")
    assert "Dorothea Lange" in credit and "…" in credit
    # no creator: the title stands in; a creator too long for the room is cut, not the licence
    assert media.screen_credit(pd(creator=None)).startswith('"Vault door of the bank", ')
    assert len(media.screen_credit(candidate.model_copy(update={"creator": "X" * 300}))) <= 140


def test_a_public_domain_credit_names_who_it_comes_from_and_drops_the_title():
    credit = media.screen_credit(pd(creator="Jack Delano"))

    assert credit == "Jack Delano, Wikimedia Commons. Public domain."


def test_the_sources_placeholder_for_no_creator_is_not_named_and_its_own_credit_line_is_used():
    # Commons: Artist "Unknown author or not provided", Credit "U.S. National Archives and Records Administration"
    base = pd(creator="Unknown author or not provided", title="A long title")
    with_credit = base.model_copy(
        update={
            "provenance": base.provenance.model_copy(
                update={
                    "raw_rights_fields": {
                        "Credit": '<a href="https://example.org/nara">U.S. National Archives and Records Administration</a>'
                    }
                }
            )
        }
    )

    assert (
        media.screen_credit(with_credit)
        == "U.S. National Archives and Records Administration, Wikimedia Commons. Public domain."
    )
    # no credit line either: the title stands in, never "Unknown author"
    assert media.screen_credit(base) == '"A long title", Wikimedia Commons. Public domain.'


def test_a_credit_has_at_most_twelve_words_so_that_an_ordinary_picture_can_carry_it():
    long_creator = "Cpl. Donald R. Ornitz, Photographer - American Commission For the Protection and Salvage of Artistic and Historic Monuments in War Areas"

    credit = media.screen_credit(pd(creator=long_creator))

    assert len(credit.split()) <= media.CREDIT_WORDS
    assert credit.endswith("…, Wikimedia Commons. Public domain.")
    assert credit.startswith("Cpl. Donald R. Ornitz,")


def test_a_cc_by_credit_keeps_its_title_when_it_fits_the_words():
    candidate = pd(title="Vault door", creator="Dorothea Lange", licence_id="CC-BY-4.0").model_copy(
        update={"rights_status": BY}
    )

    assert media.screen_credit(candidate) == '"Vault door" by Dorothea Lange, Wikimedia Commons. CC-BY-4.0.'


@pytest.mark.asyncio
async def test_a_pictures_credit_from_openverse_names_the_collection_by_its_page(
    tmp_path,
):
    candidate = pd(
        provider="openverse",
        source_url="https://www.metmuseum.org/art/collection/search/245161",
        creator="The Met",
    )

    assert media.screen_credit(candidate).endswith(", metmuseum.org. Public domain.")


@pytest.mark.asyncio
async def test_what_does_not_match_or_is_too_small_or_has_no_file_is_left_out_and_counted(
    tmp_path,
):
    found = await search(
        tmp_path,
        (
            "loc",
            [
                pd("1"),
                pd("2", title="A locomotive"),  # none of the words
                pd("3", title="Vault door, small", size=(640, 480)),
                pd("4", title="Vault door, no file", direct_file_url=None),
            ],
        ),
    )

    assert ids(found) == ["Vault door of the bank"]
    assert found["tried"] == [
        "loc: 4 found, 1 usable; left out: 2 with no usable file (none, or under 1000 px wide), 1 not matching the words"
    ]


@pytest.mark.asyncio
async def test_hits_are_ordered_by_match_then_licence_then_size_within_a_source(
    tmp_path,
):
    found = await search(
        tmp_path,
        (
            "loc",
            [
                pd("1", title="Vault door, small", size=(1200, 800)),
                pd("2", title="Vault door, large", size=(3000, 2000)),
                pd(
                    "3", title="Old bank vault door", size=(4000, 3000)
                ),  # more words than the query has: still a full match
                make_candidate(
                    CC0,
                    provider_id="4",
                    title="Vault door, cc0 same size",
                    size=(3000, 2000),
                ),
                make_candidate(
                    BY,
                    provider_id="5",
                    title="Vault door, by same size",
                    size=(3000, 2000),
                ),
            ],
        ),
        allow_by=True,
        limit=10,
    )

    # all match "vault door" fully; the public domain and CC0 come before CC BY, bigger before smaller
    assert ids(found) == [
        "Old bank vault door",
        "Vault door, large",
        "Vault door, cc0 same size",
        "Vault door, small",
        "Vault door, by same size",
    ]


@pytest.mark.asyncio
async def test_the_sources_take_turns_in_the_profiles_order_so_a_handful_is_not_all_from_one(
    tmp_path,
):
    sources = (
        ("wikimedia", [pd(f"w{n}", title=f"Vault door w{n}") for n in (1, 2, 3)]),
        ("loc", [pd(f"l{n}", title=f"Vault door l{n}") for n in (1, 2)]),
        ("openverse", []),
    )

    found = await search(tmp_path, *sources, limit=4)

    assert ids(found) == [
        "Vault door w1",
        "Vault door l1",
        "Vault door w2",
        "Vault door l2",
    ]
    flipped = await search(tmp_path, *reversed(sources), limit=4)
    assert ids(flipped)[:2] == ["Vault door l1", "Vault door w1"]


@pytest.mark.asyncio
async def test_six_hits_by_default_and_ten_at_most(tmp_path):
    many = ("loc", [pd(str(n), title=f"Vault door {n}") for n in range(30)])

    assert len((await search(tmp_path, many))["hits"]) == 6
    assert len((await search(tmp_path, many, limit=10))["hits"]) == 10
    for limit in (0, 11):
        with pytest.raises(MediaError, match="limit"):
            await search(tmp_path, many, limit=limit)


@pytest.mark.asyncio
async def test_a_source_that_fails_is_named_and_the_others_still_answer(tmp_path):
    providers = {
        "loc": FakeProvider("loc", error=RuntimeError("loc.gov answered 503")),
        "wikimedia": FakeProvider("wikimedia", [pd()]),
    }

    found = await media.search(
        tmp_path,
        "vault door",
        sources=["loc", "wikimedia"],
        allow_by=False,
        providers=providers,
    )

    assert len(found["hits"]) == 1
    assert found["tried"][0] == "loc: failed (loc.gov answered 503)"
    assert found["tried"][1].startswith("wikimedia: 1 found, 1 usable")


@pytest.mark.asyncio
async def test_a_source_that_answers_with_an_http_error_is_named_by_its_status_not_its_whole_url(
    tmp_path,
):
    error = ArchivalProviderError(
        "GET https://archive.org/advancedsearch.php?q=(vault) failed: HTTPStatusError",
        status=404,
    )

    found = await media.search(
        tmp_path,
        "vault door",
        sources=["internet_archive"],
        allow_by=False,
        providers={"internet_archive": FakeProvider("internet_archive", error=error)},
    )

    assert found["tried"] == ["internet_archive: failed (HTTP 404)"]


@pytest.mark.asyncio
async def test_a_source_that_hangs_is_given_up_on_after_its_deadline_and_the_others_still_answer(tmp_path, monkeypatch):
    class Slow(FakeProvider):
        async def search(self, query, limit=10):
            await asyncio.sleep(5)
            return []

    monkeypatch.setattr(media, "SOURCE_SECONDS", 0.05)
    providers = {"loc": Slow("loc"), "wikimedia": FakeProvider("wikimedia", [pd()])}

    found = await media.search(
        tmp_path,
        "vault door",
        sources=["loc", "wikimedia"],
        allow_by=False,
        providers=providers,
    )

    assert len(found["hits"]) == 1
    assert found["tried"][0] == "loc: failed (TimeoutError)"


@pytest.mark.asyncio
async def test_a_slow_source_is_named_with_its_time_and_a_quick_one_is_not(tmp_path, monkeypatch):
    class Slow(FakeProvider):
        async def search(self, query, limit=10):
            await asyncio.sleep(0.12)  # a host that asked for a pause
            return list(self.results)

    monkeypatch.setattr(media, "SLOW_SECONDS", 0.1)
    providers = {"loc": Slow("loc", [pd("1")]), "wikimedia": FakeProvider("wikimedia")}

    found = await media.search(
        tmp_path,
        "vault door",
        sources=["loc", "wikimedia"],
        allow_by=False,
        providers=providers,
    )

    assert found["tried"][0] == "loc: 1 found, 1 usable (took 0 s)"
    assert found["tried"][1] == "wikimedia: nothing found"


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_says_which_sources_it_asked_and_what_it_left_out(
    tmp_path,
):
    found = await search(
        tmp_path,
        ("loc", []),
        ("wikimedia", [make_candidate(SA, title="Vault door", size=(2000, 1000))]),
        ("openverse", []),
    )

    assert found["hits"] == []
    assert found["tried"] == [
        "loc: nothing found",
        "wikimedia: 1 found, 0 usable; left out: 1 with a licence that cannot be used",
        "openverse: nothing found",
    ]


@pytest.mark.asyncio
async def test_an_empty_query_is_refused_in_words(tmp_path):
    with pytest.raises(MediaError, match="what the picture shows"):
        await search(tmp_path, ("loc", []), query="   ")


@pytest.mark.asyncio
async def test_each_source_the_profile_can_name_is_a_provider_of_that_name_and_closes():
    made = media.build_providers(list(ALL), None)

    assert {name: provider.name for name, provider in made.items()} == {name: name for name in ALL}
    assert list(media.build_providers(["openverse", "loc"], None)) == [
        "openverse",
        "loc",
    ]
    for provider in made.values():
        await provider.close()


# -- fetch ------------------------------------------------------------------------------------------------------------


def serving(body: bytes, content_type: str = "image/jpeg", seen: list | None = None):
    """A transport that answers every request with ``body``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


async def found_one(project, **kwargs):
    """Search a fake source holding one public-domain picture; returns its id."""
    found = await search(project, ("loc", [pd("9", **kwargs)]))
    return found["hits"][0]["id"]


@pytest.mark.asyncio
async def test_a_hit_is_fetched_into_found_with_a_record_a_replace_media_can_read(
    tmp_path,
):
    hit = await found_one(tmp_path, creator="Jack Delano", date="1940")
    seen: list[httpx.Request] = []
    body = image_bytes("#c8b89a", "JPEG", (1600, 1000))

    made = await media.fetch(tmp_path, hit, "vault-door", transport=serving(body, seen=seen))

    file = tmp_path / "assets" / "media" / "found" / "vault-door.jpg"
    assert file.read_bytes() == body
    assert made["path"] == "assets/media/found/vault-door.jpg"
    assert made["size"] == "1600x1000"
    assert made["credit"] == "Jack Delano, Wikimedia Commons. Public domain."
    record = json.loads(file.with_name("vault-door.jpg.json").read_text())
    assert record["ai"] is False
    assert record["credit"] == made["credit"]
    assert record["licence"] == "Public domain" and record["rights_status"] == "public_domain"
    assert record["source_url"] == "https://commons.wikimedia.org/wiki/File:Example.jpg"
    assert record["attribution"].startswith('"Vault door of the bank" by Jack Delano (1940).')
    assert record["sha256"] == hashlib.sha256(body).hexdigest()
    assert datetime.fromisoformat(record["at"]).tzinfo is not None
    assert record["verify_at_source"] is True  # every source's licence is its own word
    assert seen[0].url == "https://upload.wikimedia.org/example.jpg"
    assert "hyperframes-mcp" in seen[0].headers["user-agent"]  # as the archival clients send it


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["loc", "wikimedia", "openverse", "internet_archive"])
async def test_a_picture_from_any_source_says_to_verify_its_licence_at_the_source_page(tmp_path, source):
    found = await media.search(
        tmp_path, "vault door", sources=[source], allow_by=False,
        providers={source: FakeProvider(source, [pd()])},
    )  # fmt: skip
    made = await media.fetch(
        tmp_path,
        found["hits"][0]["id"],
        "v",
        transport=serving(image_bytes(fmt="JPEG", size=(1600, 1000))),
    )

    record = json.loads((tmp_path / made["path"]).with_name("v.jpg.json").read_text())
    assert record["verify_at_source"] is True
    assert made["verify_at_source"] is True


@pytest.mark.asyncio
async def test_the_file_takes_its_suffix_from_what_the_host_sent(tmp_path):
    hit = await found_one(tmp_path)

    made = await media.fetch(
        tmp_path,
        hit,
        "v",
        transport=serving(image_bytes(fmt="PNG", size=(1600, 1000)), "image/png"),
    )

    assert made["path"] == "assets/media/found/v.png"


@pytest.mark.asyncio
async def test_a_hit_that_was_never_found_is_refused_and_says_to_search_first(tmp_path):
    with pytest.raises(MediaError, match="search_media first"):
        await media.fetch(tmp_path, "deadbeef", "v", transport=serving(b"x"))
    assert not (tmp_path / "assets").exists()


@pytest.mark.asyncio
async def test_a_name_that_is_already_a_file_is_refused_whatever_its_suffix_and_the_file_is_untouched(
    tmp_path,
):
    hit = await found_one(tmp_path)
    taken = tmp_path / "assets" / "media" / "found" / "v.png"
    taken.parent.mkdir(parents=True)
    taken.write_bytes(b"mine")

    with pytest.raises(MediaError, match="already a file"):
        await media.fetch(
            tmp_path,
            hit,
            "v",
            transport=serving(image_bytes(fmt="JPEG", size=(1600, 1000))),
        )

    assert taken.read_bytes() == b"mine"
    assert sorted(p.name for p in taken.parent.iterdir()) == ["v.png"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "kind", "complaint"),
    [
        (b"<html>an error page</html>", "text/html", "content type"),
        (
            b"II*\x00",
            "image/tiff",
            "content type",
        ),  # replace_media takes jpg, png and webp
        (b"not a picture at all", "image/jpeg", "cannot be read"),
        (image_bytes(fmt="JPEG", size=(640, 480)), "image/jpeg", "640 px wide"),
    ],
)
async def test_a_file_that_is_not_a_usable_picture_is_refused_and_nothing_is_left_behind(
    tmp_path, body, kind, complaint
):
    hit = await found_one(tmp_path)

    with pytest.raises(MediaError, match=complaint):
        await media.fetch(tmp_path, hit, "v", transport=serving(body, kind))

    assert list((tmp_path / "assets" / "media" / "found").glob("*")) == []


@pytest.mark.asyncio
async def test_the_licence_is_checked_again_at_the_fetch_so_an_edited_hit_book_opens_nothing(
    tmp_path,
):
    hit = await found_one(tmp_path)
    book = tmp_path / ".video-mcp" / "media_hits.json"
    data = json.loads(book.read_text())
    data[hit]["candidate"]["rights_status"] = "share_alike"
    book.write_text(json.dumps(data))
    seen: list[httpx.Request] = []

    with pytest.raises(MediaError, match="licence"):
        await media.fetch(tmp_path, hit, "v", transport=serving(b"x", seen=seen))

    assert seen == []  # nothing was requested


@pytest.mark.asyncio
async def test_a_cc_by_hit_found_without_asking_for_it_cannot_be_fetched_either(
    tmp_path,
):
    hit = await found_one(tmp_path)
    book = tmp_path / ".video-mcp" / "media_hits.json"
    data = json.loads(book.read_text())
    data[hit]["candidate"]["rights_status"] = "attribution_required"
    book.write_text(json.dumps(data))

    with pytest.raises(MediaError, match="CC BY"):
        await media.fetch(tmp_path, hit, "v", transport=serving(b"x"))


@pytest.mark.asyncio
async def test_a_cc_by_hit_that_was_asked_for_is_fetched_with_its_credit_and_licence(
    tmp_path,
):
    by = make_candidate(
        BY,
        provider_id="3",
        title="Vault door by",
        size=(1600, 1000),
        creator="Jane Roe",
    )
    found = await search(tmp_path, ("loc", [by]), allow_by=True)

    made = await media.fetch(
        tmp_path,
        found["hits"][0]["id"],
        "by",
        transport=serving(image_bytes(fmt="JPEG", size=(1600, 1000))),
    )

    assert made["licence"] == "CC-BY-4.0"
    assert "Jane Roe" in made["credit"] and made["credit"].endswith("CC-BY-4.0.")


@pytest.mark.asyncio
async def test_the_book_keeps_the_latest_hundred_hits_only(tmp_path):
    for batch in range(12):
        await search(
            tmp_path,
            (
                "loc",
                [pd(f"{batch}-{n}", title=f"Vault door {batch} {n}") for n in range(10)],
            ),
            limit=10,
        )

    book = json.loads((tmp_path / ".video-mcp" / "media_hits.json").read_text())

    assert len(book) == media.MAX_HITS_KEPT
    last = await search(tmp_path, ("loc", [pd("11-0", title="Vault door 11 0")]))
    assert last["hits"][0]["id"] in book  # the newest are the ones kept


class WordsMatter(FakeProvider):
    """A provider that finds its results only for the query it is told, as a real search that wants every word."""

    def __init__(self, name: str, answers: dict[str, list]):
        super().__init__(name)
        self.answers = answers

    async def search(self, query: str, limit: int = 10):
        self.queries.append(query)
        return list(self.answers.get(query, []))


def test_a_stop_word_that_starts_a_sentence_is_not_taken_for_a_name():
    assert media.loosened("The vault door open") == ["vault door"]


def test_a_lone_capitalised_first_word_may_only_start_the_sentence_so_the_wider_form_goes_first():
    # "Gold" alone matches every title with gold in it; "Gold bars" is the form worth asking first
    assert media.loosened("Gold bars stacked in a vault") == ["Gold bars", "Gold"]
    # a first word with other names after it is still a name
    assert media.loosened("Marburg mine entrance Patton 1945") == [
        "Marburg Patton 1945",
        "Marburg mine",
    ]


def test_a_long_query_has_fewer_word_forms_names_and_years_first():
    assert media.loosened("Marburg vault wall hole 1945 gold") == [
        "Marburg 1945",
        "Marburg vault",
    ]
    assert media.loosened("Bundesbank ledger gold accounts book") == [
        "Bundesbank ledger",
        "Bundesbank",
    ]
    assert media.loosened("gold bars stacked in a vault") == ["gold bars"]
    assert media.loosened("vault door") == []  # already two words
    assert media.loosened("Marburg") == []


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_is_asked_again_with_fewer_words_and_says_so(
    tmp_path,
):
    capture = pd("9", title="Capture of Germany's gold, Marburg 1945")
    provider = WordsMatter("wikimedia", {"Marburg 1945": [capture]})

    found = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["wikimedia"],
        allow_by=False,
        providers={"wikimedia": provider},
    )

    assert ids(found) == ["Capture of Germany's gold, Marburg 1945"]
    assert provider.queries == ["Marburg vault wall hole 1945 gold", "Marburg 1945"]
    assert found["query"] == "Marburg vault wall hole 1945 gold"
    assert found["retried"] == ["Marburg 1945"]
    assert found["tried"][0].startswith("wikimedia: nothing found")
    assert found["tried"][1].startswith("as 'Marburg 1945': wikimedia: 1 found, 1 usable")


@pytest.mark.asyncio
async def test_a_search_with_hits_is_not_asked_again(tmp_path):
    provider = WordsMatter(
        "wikimedia",
        {"Marburg vault wall hole 1945 gold": [pd("1", title="Marburg vault wall hole 1945 gold")]},
    )

    found = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["wikimedia"],
        allow_by=False,
        providers={"wikimedia": provider},
    )

    assert len(found["hits"]) == 1 and provider.queries == ["Marburg vault wall hole 1945 gold"]
    assert found["retried"] == []


@pytest.mark.asyncio
async def test_a_search_is_not_asked_again_once_the_time_for_asking_again_is_used_up(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "RETRY_WITHIN_S", -1.0)
    provider = WordsMatter("wikimedia", {"Marburg 1945": [pd("9", title="Marburg 1945")]})

    found = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["wikimedia"],
        allow_by=False,
        providers={"wikimedia": provider},
    )

    assert found["hits"] == [] and provider.queries == ["Marburg vault wall hole 1945 gold"]
    assert found["retried"] == []


@pytest.mark.asyncio
async def test_the_second_retry_runs_and_the_hit_remembers_the_wording_that_found_it(
    tmp_path,
):
    provider = WordsMatter("wikimedia", {"Marburg vault": [pd("9", title="Marburg vault, 1945")]})

    found = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["wikimedia"],
        allow_by=False,
        providers={"wikimedia": provider},
    )

    assert found["retried"] == ["Marburg 1945", "Marburg vault"]
    assert len(found["hits"]) == 1
    book = json.loads((tmp_path / ".video-mcp" / "media_hits.json").read_text())
    assert [entry["query"] for entry in book.values()] == ["Marburg vault"]


@pytest.mark.asyncio
async def test_a_source_that_failed_is_not_asked_again_and_a_search_where_all_failed_is_not_repeated(
    tmp_path,
):
    down = FakeProvider("loc", error=RuntimeError("HTTP 429"))
    empty = WordsMatter("wikimedia", {})

    found = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["loc", "wikimedia"],
        allow_by=False,
        providers={"loc": down, "wikimedia": empty},
    )

    assert len(down.queries) == 1  # the host that said wait is left alone
    assert len(empty.queries) == 3  # the full query and both loosened forms
    assert any(line.startswith("loc: failed") for line in found["tried"])

    alone = FakeProvider("loc", error=RuntimeError("HTTP 503"))
    again = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["loc"],
        allow_by=False,
        providers={"loc": alone},
    )

    assert alone.queries == ["Marburg vault wall hole 1945 gold"]
    assert again["retried"] == []


@pytest.mark.asyncio
async def test_a_retry_that_outlasts_the_time_allowed_is_cut_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "RETRY_WITHIN_S", 0.2)

    class Hangs(WordsMatter):
        async def search(self, query: str, limit: int = 10):
            if len(self.queries) >= 1:
                await asyncio.sleep(5)
            return await super().search(query, limit)

    provider = Hangs("wikimedia", {})

    found = await media.search(
        tmp_path,
        "Marburg vault wall hole 1945 gold",
        sources=["wikimedia"],
        allow_by=False,
        providers={"wikimedia": provider},
    )

    assert found["hits"] == [] and found["retried"] == []
    assert any("not answered in the" in line for line in found["tried"])
