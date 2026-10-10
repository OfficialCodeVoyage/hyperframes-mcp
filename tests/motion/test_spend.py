"""What an edit may spend: the limits, the quote that comes before a call, the confirm that makes it, and where the money
is recorded. The providers are faked (``generate.make_picture`` and ``make_clip``), so no test makes a paid call; the
host program's books are a fake sink."""

from __future__ import annotations

import asyncio
import fcntl
import json
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image

from hyperframes_mcp.motion import generate, manifest, spend
from hyperframes_mcp.motion.project_edit import AI_CREDIT
from hyperframes_mcp.providers.video.budget import ClipBudget
from hyperframes_mcp.providers.video.settings import load_i2v_settings
from tests.motion.spend_helpers import CLIP_USD, NOW, PICTURE_USD, confirmed_lines, earlier_spend


class FakeSink:
    """A host program's books: it refuses a call above ``ceiling`` and keeps what it was told."""

    def __init__(self, ceiling: float | None = None, fail_record: bool = False) -> None:
        self.ceiling = ceiling
        self.fail_record = fail_record
        self.records: list[dict] = []

    async def check(self, project, manifest, usd):
        if self.ceiling is not None and usd > self.ceiling:
            raise spend.SpendError("the host program's budget has a hard stop")

    async def record(self, project, manifest, usd, note, ref, *, provider, visual_type, quote_id):
        if self.fail_record:
            raise RuntimeError("database is locked")
        self.records.append(
            {"usd": usd, "ref": ref, "provider": provider, "visual_type": visual_type, "quote_id": quote_id}
        )
        return {"where": "host"}


@pytest.fixture
def sink():
    """A fake host sink, installed for the test and removed after it."""
    fake = FakeSink()
    spend.use_sink(fake)
    yield fake
    spend.use_sink(None)


# -- the limits ----------------------------------------------------------------------------------------------------


def test_the_limits_are_one_dollar_a_call_and_ten_a_video_with_or_without_the_file(
    tmp_path,
):
    assert spend.load_spend_limits(path=tmp_path / "missing.yaml") == spend.SpendLimits(
        per_call_usd=1.0, per_project_usd=10.0, per_day_usd=None, quote_ttl_seconds=900
    )
    assert spend.load_spend_limits() == spend.SpendLimits()  # the file as shipped


def test_a_profile_may_set_its_own_limits_and_a_misspelt_one_is_an_error(tmp_path):
    path = tmp_path / "limits.yaml"
    path.write_text(
        "spend:\n  per_call_usd: 2\n  profiles:\n    history:\n      per_project_usd: 4\n      per_day_usd: 3\n"
    )

    assert spend.load_spend_limits("default", path).per_project_usd == 10.0
    history = spend.load_spend_limits("history", path)
    assert (history.per_call_usd, history.per_project_usd, history.per_day_usd) == (
        2,
        4,
        3,
    )
    path.write_text("spend:\n  per_call_dollars: 2\n")
    with pytest.raises(ValueError, match="per_call_dollars"):
        spend.load_spend_limits(path=path)


# -- the quote -----------------------------------------------------------------------------------------------------


def test_a_picture_quote_shows_the_price_and_the_full_prompt_and_is_kept_with_its_expiry(
    project,
):
    quote = spend.quote_picture(project, "a vault door by lamplight", quality="high", out="assets/door.png")

    assert quote.kind == "picture" and quote.id.startswith("q-")
    assert quote.price_usd == PICTURE_USD
    assert quote.prompt == generate.picture_prompt("a vault door by lamplight", "history")
    assert "a vault door by lamplight" in quote.prompt and "Full-bleed" in quote.prompt
    assert quote.expires_at - quote.created_at == timedelta(seconds=900)
    assert quote.arguments == {
        "request": "a vault door by lamplight",
        "quality": "high",
        "out": "assets/door.png",
        "model": generate.PICTURE_MODEL,
        "profile": "history",
    }
    assert len(quote.arguments_sha256) == 64
    kept = json.loads((project / ".video-mcp" / "quotes.json").read_text())
    assert kept["quotes"][quote.id]["price_usd"] == PICTURE_USD
    assert not list((project / "assets").glob("door*"))  # nothing was made


def test_a_quote_that_is_not_saved_leaves_the_project_as_it_was(project):
    spend.quote_picture(project, "x", quality="medium", out="assets/a.png", save=False)

    assert not (project / ".video-mcp").exists()


def test_a_picture_quote_refuses_what_could_not_be_confirmed(project, monkeypatch):
    with pytest.raises(spend.SpendError, match="outside the project"):
        spend.quote_picture(project, "x", quality="high", out="../elsewhere.png")
    with pytest.raises(spend.SpendError, match="quality is one of medium, high"):
        spend.quote_picture(project, "x", quality="ultra", out="assets/a.png")
    with pytest.raises(spend.SpendError, match="what the picture shows"):
        spend.quote_picture(project, "  ", quality="high", out="assets/a.png")
    monkeypatch.setattr(generate, "picture_price", lambda quality: 1.5)
    with pytest.raises(spend.SpendError, match=r"\$1\.50.*\$1\.00 limit for one call"):
        spend.quote_picture(project, "x", quality="high", out="assets/a.png")
    assert not (project / ".video-mcp" / "quotes.json").exists()


def test_a_picture_file_is_a_jpeg_or_a_png_and_another_name_is_refused_at_the_quote(
    project,
):
    for name in ("assets/a.jpg", "assets/a.JPEG", "assets/a.png"):
        spend.quote_picture(project, "x", quality="high", out=name, save=False)
    for name in ("assets/a.gif", "assets/a", "assets/a.webp"):
        with pytest.raises(spend.SpendError, match=r"must end in \.jpg or \.png"):
            spend.quote_picture(project, "x", quality="high", out=name)
    assert not (project / ".video-mcp" / "quotes.json").exists()


def test_a_quote_over_what_the_project_may_still_spend_is_refused_and_says_what_is_left(
    project,
):
    earlier_spend(project, 9.99)

    with pytest.raises(spend.SpendError, match=r"\$9\.99 of \$10\.00.*per_project_usd"):
        spend.quote_picture(project, "x", quality="high", out="assets/a.png")


def test_a_day_limit_counts_only_todays_spend(project, tmp_path, monkeypatch):
    limits = tmp_path / "limits.yaml"
    limits.write_text("spend:\n  per_day_usd: 0.1\n")
    monkeypatch.setattr(spend, "CONFIG_PATH", limits)
    earlier_spend(project, 0.09, at=datetime.now(UTC))
    with pytest.raises(spend.SpendError, match="per_day_usd"):
        spend.quote_picture(project, "x", quality="high", out="assets/a.png")

    (project / ".video-mcp" / "spend.jsonl").unlink()
    earlier_spend(project, 0.09, at=datetime.now(UTC) - timedelta(days=1))
    quote = spend.quote_picture(project, "x", quality="medium", out="assets/a.png")

    assert quote.price_usd == generate.picture_price("medium")


@pytest.mark.asyncio
async def test_a_clip_quote_of_a_length_the_model_cannot_make_names_the_lengths(
    project,
):
    with pytest.raises(spend.SpendError) as refused:
        await spend.quote_clip(project, "assets/vault.png", "push in", seconds=5, out="assets/door.mp4")

    assert "makes 4, 6, 8 s (the nearest is 6 s)" in str(refused.value)
    assert not (project / ".video-mcp").exists()


@pytest.mark.asyncio
async def test_a_clip_quote_states_the_price_the_canvas_and_the_clip_cap_left(project):
    quote = await spend.quote_clip(project, "assets/vault.png", "push in", seconds=6, out="assets/door.mp4")

    assert quote.kind == "clip" and quote.price_usd == generate.clip_price(6)
    limits = load_i2v_settings().limits
    assert quote.clip_cap_left == {
        "clips": limits.max_clips,
        "seconds": limits.max_total_seconds,
        "usd": limits.max_spend_usd,
    }
    assert quote.arguments["seconds"] == 6 and quote.arguments["still"] == "assets/vault.png"
    assert len(quote.arguments["still_sha256"]) == 64
    assert quote.prompt is None


@pytest.mark.asyncio
async def test_a_clip_quote_counts_what_the_projects_clip_log_already_holds(project):
    settings = load_i2v_settings()
    budget = ClipBudget(settings.limits, project / generate.CLIP_LOG)
    await budget.begin_attempt(generate.CLIP_CAP_ID, "first", settings.model, 4, 0.12)

    quote = await spend.quote_clip(project, "assets/vault.png", "x", seconds=4, out="assets/door.mp4")

    assert quote.clip_cap_left["clips"] == settings.limits.max_clips - 1
    assert quote.clip_cap_left["seconds"] == settings.limits.max_total_seconds - 4
    assert quote.clip_cap_left["usd"] == pytest.approx(settings.limits.max_spend_usd - 0.12)


@pytest.mark.asyncio
async def test_a_clip_quote_needs_a_still_and_a_file_inside_the_project(project, tmp_path):
    (tmp_path / "outside.png").write_bytes(b"png")
    for still, out, message in (
        ("assets/nothing.png", "assets/a.mp4", "does not exist"),
        (str(tmp_path / "outside.png"), "assets/a.mp4", "outside the project"),
        ("assets/vault.png", "../a.mp4", "outside the project"),
    ):
        with pytest.raises(spend.SpendError, match=message):
            await spend.quote_clip(project, still, "x", seconds=4, out=out)


# -- the confirm ---------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirming_a_picture_makes_it_in_the_projects_profile_and_records_it_in_the_spend_log(
    project, paid, sink
):
    quote = spend.quote_picture(project, "a vault", quality="high", out="assets/door.png")

    made = await spend.confirm(project, quote.id)

    assert paid == [("picture", "a vault", "door.png", "high", "history")]
    assert Image.open(project / "assets" / "door.png").size == (320, 180)
    assert made["path"] == "assets/door.png" and made["credit"] == AI_CREDIT
    assert made["cost_usd"] == PICTURE_USD
    assert made["recorded"] == {"where": "spend_log", "sink": {"where": "host"}}
    assert made["spent_usd"] == PICTURE_USD
    ref = str((project / "assets" / "door.png").resolve())
    assert sink.records == [
        {"usd": PICTURE_USD, "ref": ref, "provider": "fal", "visual_type": "ai_image", "quote_id": quote.id}
    ]
    (line,) = confirmed_lines(project)
    assert (line["usd"], line["quote"], line["ref"]) == (PICTURE_USD, quote.id, ref)
    assert line["provider"] == "fal"  # pictures are drawn on fal now


@pytest.mark.asyncio
async def test_a_picture_is_made_at_the_canvas_of_the_project(tmp_path, monkeypatch):
    folder = tmp_path / "wide"
    manifest.write_manifest(
        folder,
        manifest.ProjectManifest(
            profile="history",
            topic_id=1,
            canvas=manifest.Canvas(width=1280, height=720, fps=24),
        ),
    )
    seen = {}

    async def make_picture(request, out, *, canvas=None, **kwargs):
        seen["canvas"] = canvas
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return {"credit": AI_CREDIT, "cost_usd": PICTURE_USD}

    monkeypatch.setattr(generate, "make_picture", make_picture)
    quote = spend.quote_picture(folder, "x", quality="high", out="assets/a.jpg")

    await spend.confirm(folder, quote.id)

    assert (seen["canvas"].width, seen["canvas"].height) == (1280, 720)


@pytest.mark.asyncio
async def test_a_project_without_a_manifest_gets_the_default_profile_and_only_the_spend_log(standalone, paid):
    quote = spend.quote_picture(standalone, "a vault", quality="medium", out="assets/door.png")

    made = await spend.confirm(standalone, quote.id)

    assert paid[0][-1] == "default"  # the default profile, for a project that names none
    assert made["recorded"] == {"where": "spend_log"}
    (line,) = confirmed_lines(standalone)
    assert line["usd"] == generate.picture_price("medium")


@pytest.mark.asyncio
async def test_confirming_a_clip_gives_the_project_its_topic_and_canvas_and_records_it_as_fal(project, paid, sink):
    quote = await spend.quote_clip(project, "assets/vault.png", "push in", seconds=4, out="assets/door.mp4")

    made = await spend.confirm(project, quote.id)

    (call,) = paid
    assert call[:4] == ("clip", "push in", "door.mp4", 4)
    assert call[4] == 1 and call[5] == manifest.read_manifest(project).canvas
    assert made["cost_usd"] == CLIP_USD
    (row,) = sink.records
    assert (row["provider"], row["visual_type"], row["usd"]) == ("fal", "ai_video", CLIP_USD)


@pytest.mark.asyncio
async def test_confirming_twice_pays_once_and_gives_the_earlier_result_again(project, paid):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")

    first = await spend.confirm(project, quote.id)
    again = await spend.confirm(project, quote.id, now=NOW + timedelta(days=3))

    assert again == {**first, "replayed": True}
    assert len(paid) == 1 and len(confirmed_lines(project)) == 1


@pytest.mark.asyncio
async def test_two_confirms_of_one_quote_at_once_pay_once(project, paid, monkeypatch):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    slow = generate.make_picture

    async def slowly(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await slow(*args, **kwargs)

    monkeypatch.setattr(generate, "make_picture", slowly)

    first, second = await asyncio.gather(
        spend.confirm(project, quote.id),
        spend.confirm(project, quote.id),
    )

    assert len(paid) == 1
    assert sorted(bool(r.get("replayed")) for r in (first, second)) == [False, True]


@pytest.mark.asyncio
async def test_a_confirm_waits_for_a_paid_call_on_any_video_in_another_process_then_gives_up(
    project, paid, monkeypatch, central_spend_log
):
    # one lock for every project: two videos paying at once could both pass the all-projects daily limit
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    monkeypatch.setattr(spend, "LOCK_WAIT_SECONDS", 0.3)
    central_spend_log.parent.mkdir(parents=True, exist_ok=True)
    with central_spend_log.with_suffix(".lock").open("a") as other:
        fcntl.flock(other, fcntl.LOCK_EX)
        with pytest.raises(spend.SpendError, match="another video.*still running"):
            await spend.confirm(project, quote.id)

    assert paid == []


@pytest.mark.asyncio
async def test_an_unknown_or_expired_quote_pays_nothing(project, paid):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")

    with pytest.raises(spend.SpendError, match="no quote q-nope"):
        await spend.confirm(project, "q-nope")
    with pytest.raises(spend.SpendError, match="expired.*quote again"):
        await spend.confirm(
            project,
            quote.id,
            now=quote.created_at + timedelta(seconds=901),
        )

    assert paid == [] and not (project / "assets" / "door.png").exists()


@pytest.mark.asyncio
async def test_a_price_that_moved_since_the_quote_is_refused_and_pays_nothing(project, paid, monkeypatch):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    monkeypatch.setattr(generate, "picture_price", lambda quality: PICTURE_USD + 0.05)

    with pytest.raises(spend.SpendError, match="price changed.*quote again"):
        await spend.confirm(project, quote.id)

    assert paid == []


@pytest.mark.asyncio
async def test_a_prompt_that_changed_since_the_quote_is_refused(project, paid, monkeypatch):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    monkeypatch.setattr(generate, "picture_prompt", lambda request, slug="default": "another style")

    with pytest.raises(spend.SpendError, match="prompt changed"):
        await spend.confirm(project, quote.id)

    assert paid == []


@pytest.mark.asyncio
async def test_a_still_that_changed_since_the_quote_is_refused(project, paid):
    quote = await spend.quote_clip(project, "assets/vault.png", "push in", seconds=4, out="assets/door.mp4")
    (project / "assets" / "vault.png").write_bytes(b"another picture")

    with pytest.raises(spend.SpendError, match="still changed"):
        await spend.confirm(project, quote.id)

    assert paid == []


@pytest.mark.asyncio
async def test_a_quote_whose_file_was_edited_is_refused(project, paid):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    path = project / ".video-mcp" / "quotes.json"
    book = json.loads(path.read_text())
    book["quotes"][quote.id]["arguments"]["request"] = "something else"
    path.write_text(json.dumps(book))

    with pytest.raises(spend.SpendError, match="do not match their hash"):
        await spend.confirm(project, quote.id)

    assert paid == []


@pytest.mark.asyncio
async def test_a_confirm_that_would_pass_the_projects_limit_is_refused_even_for_an_earlier_quote(project, paid):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    earlier_spend(project, 9.99)

    with pytest.raises(spend.SpendError, match="per_project_usd"):
        await spend.confirm(project, quote.id)

    assert paid == []


@pytest.mark.asyncio
async def test_pictures_are_made_until_the_projects_ten_dollars_are_used_and_the_next_quote_is_refused(
    project, paid, monkeypatch
):
    monkeypatch.setattr(generate, "picture_price", lambda quality: 0.9)
    for number in range(11):  # eleven at $0.90 is $9.90
        quote = spend.quote_picture(project, "x", quality="high", out=f"assets/p{number}.png")
        await spend.confirm(project, quote.id)

    assert len(paid) == 11
    with pytest.raises(spend.SpendError, match="per_project_usd"):
        spend.quote_picture(project, "x", quality="high", out="assets/more.png")


@pytest.mark.asyncio
async def test_a_sink_that_refuses_the_call_is_found_out_before_anything_is_paid(project, paid, sink):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    sink.ceiling = 0.0

    with pytest.raises(spend.SpendError, match="hard stop"):
        await spend.confirm(project, quote.id)

    assert paid == [] and not (project / ".video-mcp" / "spend.jsonl").exists()


@pytest.mark.asyncio
async def test_a_sink_that_cannot_check_refuses_the_call_before_anything_is_paid(standalone, paid, sink):
    quote = spend.quote_picture(standalone, "x", quality="high", out="assets/door.png")

    async def broken(project, manifest, usd):
        raise RuntimeError("database is locked")

    sink.check = broken

    with pytest.raises(spend.SpendError, match="nothing was paid"):
        await spend.confirm(standalone, quote.id)
    assert paid == []


@pytest.mark.asyncio
async def test_a_failed_call_pays_nothing_and_the_quote_can_be_confirmed_again(project, paid, monkeypatch):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    working = generate.make_picture

    async def fails(*args, **kwargs):
        raise generate.GenerateError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(generate, "make_picture", fails)
    with pytest.raises(spend.SpendError, match="OPENAI_API_KEY"):
        await spend.confirm(project, quote.id)
    assert spend.spend_state(project)["spent_usd"] == 0  # the hold was released

    monkeypatch.setattr(generate, "make_picture", working)
    made = await spend.confirm(project, quote.id)

    assert made["cost_usd"] == PICTURE_USD


@pytest.mark.asyncio
async def test_a_sink_that_fails_after_the_call_does_not_undo_it_and_the_spend_still_counts(standalone, paid, sink):
    quote = spend.quote_picture(standalone, "x", quality="high", out="assets/door.png")
    sink.fail_record = True

    made = await spend.confirm(standalone, quote.id)

    assert made["recorded"]["where"] == "spend_log"
    assert "database is locked" in made["recorded"]["sink_error"]
    assert spend.spend_state(standalone)["spent_usd"] == PICTURE_USD
    assert (standalone / "assets" / "door.png").exists()  # the picture is kept


@pytest.mark.asyncio
async def test_record_spend_notes_a_spend_the_project_made_outside_a_quote(project, sink):
    recorded = await spend.record_spend(
        project,
        3.49,
        "pictures made before the server",
        None,
        provider="openai",
        visual_type="ai_image",
    )

    assert recorded == {"where": "spend_log", "sink": {"where": "host"}}
    assert spend.spend_state(project)["spent_usd"] == 3.49
    (row,) = sink.records
    assert row["usd"] == 3.49 and row["ref"] is None


# -- the state, the project, the manifest -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_state_says_what_was_spent_and_what_may_still_be(project, paid):
    quote = spend.quote_picture(project, "x", quality="high", out="assets/door.png")
    await spend.confirm(project, quote.id)

    state = spend.spend_state(project)

    assert state["spent_usd"] == PICTURE_USD and state["calls"] == 1
    assert state["limits"] == {
        "all_projects_per_day_usd": 10.0,
        "per_call_usd": 1.0,
        "per_project_usd": 10.0,
        "per_day_usd": None,
        "max_calls_per_hour": 30,
        "max_calls_per_day": 100,
    }
    assert state["left_usd"]["project"] == pytest.approx(10.0 - PICTURE_USD)
    assert state["left_usd"]["next_call"] == 1.0
    assert state["profile"] == "history"


def test_a_project_with_an_unreadable_manifest_is_not_given_the_defaults(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / manifest.MANIFEST_FILE).write_text('{"colour": "typo"}')

    with pytest.raises(spend.SpendError, match="not a valid manifest"):
        spend.spend_state(folder)


def test_a_damaged_spend_log_stops_every_spend_rather_than_counting_less(project):
    earlier_spend(project, 1.0)
    with (project / ".video-mcp" / "spend.jsonl").open("a") as log:
        log.write("{not json\n")

    with pytest.raises(spend.SpendError, match="spend.jsonl line 2"):
        spend.quote_picture(project, "x", quality="high", out="assets/a.png")


# -- the central log and the backstops (2026-10-07 security review) -----------------------------------------------


def central_spend(central_spend_log, project, usd: float, at: datetime = NOW) -> None:
    """Put ``usd`` in the central spend log for ``project``, as an earlier confirm would have."""
    central_spend_log.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "at": at.isoformat(),
        "usd": usd,
        "note": "earlier",
        "project": str(project.resolve()),
    }
    with central_spend_log.open("a") as log:
        log.write(json.dumps(line) + "\n")


def test_removing_a_projects_own_spend_log_does_not_start_its_count_again(project, central_spend_log):
    central_spend(central_spend_log, project, 9.99)

    with pytest.raises(spend.SpendError, match=r"has spent \$9\.99 of \$10\.00"):
        spend.quote_picture(project, "a vault", out="assets/media/edits/v.png")


def test_a_new_project_folder_cannot_pass_the_limit_for_every_project_in_a_day(project, standalone, central_spend_log):
    central_spend(central_spend_log, standalone, 9.99, at=datetime.now(UTC))

    with pytest.raises(spend.SpendError, match="every project together"):
        spend.quote_picture(project, "a vault", out="assets/media/edits/v.png")


@pytest.mark.asyncio
async def test_a_confirmed_spend_is_also_in_the_central_log_with_its_project(project, paid, central_spend_log):
    quote = spend.quote_picture(project, "a vault", out="assets/media/edits/v.png")

    await spend.confirm(project, quote.id)

    lines = [json.loads(line) for line in central_spend_log.read_text().splitlines()]
    assert {line["project"] for line in lines} == {str(project.resolve())}
    assert [line["status"] for line in lines] == ["reserved", "confirmed"]
