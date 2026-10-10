"""Holes an adversarial review found in the overspend guard, each pinned: what the logs say decides whether a quote may be
sent again (a forged or lost quote file cannot), every attempt counts as a call, a stale clock or a damaged log cannot
raise a limit, and an unknown value of the freeze variable freezes."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from hyperframes_mcp.motion import generate, spend
from hyperframes_mcp.providers.video.errors import ContentPolicyError
from tests.motion.spend_helpers import PICTURE_USD, spent_lines


def quote_one(project, name="door"):
    return spend.quote_picture(project, "x", quality="high", out=f"assets/{name}.png")


def limits_file(tmp_path, monkeypatch, text: str):
    path = tmp_path / "limits.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(spend, "CONFIG_PATH", path)
    return path


KILL = """
import asyncio, os, sys
from pathlib import Path
from hyperframes_mcp.motion import generate, spend

spend.CENTRAL_LOG = Path(sys.argv[1])


async def die(*args, **kwargs):  # the request has gone out; the process is killed before the answer
    os._exit(9)


generate.make_picture = die
asyncio.run(spend.confirm(Path(sys.argv[2]), sys.argv[3]))
"""


def kill_after_sending(project, quote_id):
    """Confirm the quote in another process that dies the moment the paid request has been sent."""
    done = subprocess.run(
        [sys.executable, "-c", KILL, str(spend.central_log()), str(project), quote_id],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 9, done.stderr


def edit_book(project, change):
    path = project / ".video-mcp" / "quotes.json"
    book = json.loads(path.read_text())
    change(book["quotes"])
    path.write_text(json.dumps(book))


# -- the logs decide, not the quote file ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writing_the_quote_file_back_as_it_was_does_not_make_a_confirmed_quote_payable_again(project, paid):
    quote = quote_one(project)
    book = (project / ".video-mcp" / "quotes.json").read_text()
    first = await spend.confirm(project, quote.id)
    (project / ".video-mcp" / "quotes.json").write_text(book)  # as if it had never been confirmed
    (project / "assets" / "door.png").unlink()  # and the file is gone: nothing marks the call as made but the logs

    with pytest.raises(spend.SpendError, match=r"already (made|billed)"):
        await spend.confirm(project, quote.id)

    assert len(paid) == 1 and first["cost_usd"] == PICTURE_USD
    assert spend.spend_state(project)["calls"] == 1


@pytest.mark.asyncio
async def test_a_confirmed_quote_gives_its_first_result_back_while_its_file_is_there(project, paid):
    quote = quote_one(project)
    first = await spend.confirm(project, quote.id)

    again = await spend.confirm(project, quote.id)

    assert len(paid) == 1
    assert again["replayed"] is True
    assert {key: again[key] for key in ("quote_id", "kind", "path", "cost_usd", "credit")} == {
        key: first[key] for key in ("quote_id", "kind", "path", "cost_usd", "credit")
    }


@pytest.mark.asyncio
async def test_a_process_killed_after_sending_leaves_a_hold_and_the_quote_is_not_sent_again(project, paid):
    quote = quote_one(project)
    kill_after_sending(project, quote.id)

    with pytest.raises(spend.SpendError, match=r"may have been billed.*spend clear"):
        await spend.confirm(project, quote.id)

    assert paid == []
    assert [hold["quote_id"] for hold in spend.spend_state(project)["uncertain"]] == [quote.id]


@pytest.mark.asyncio
async def test_clearing_a_killed_call_as_billed_does_not_make_its_quote_payable_again(project, paid):
    quote = quote_one(project)
    kill_after_sending(project, quote.id)
    spend.clear_hold(quote.id, billed=True)

    with pytest.raises(spend.SpendError, match=r"already (made|billed)"):
        await spend.confirm(project, quote.id)

    assert paid == [] and spend.spend_state(project)["spent_usd"] == PICTURE_USD


@pytest.mark.asyncio
async def test_a_failure_after_the_spend_was_logged_gives_the_first_result_back_not_a_second_call(
    project, paid, monkeypatch
):
    quote = quote_one(project)

    class Sink:
        async def check(self, project, manifest, usd):
            return None

        async def record(self, *args, **kwargs):
            raise asyncio.CancelledError

    spend.use_sink(Sink())
    try:
        with pytest.raises(asyncio.CancelledError):
            await spend.confirm(project, quote.id)
        again = await spend.confirm(project, quote.id)
    finally:
        spend.use_sink(None)

    assert len(paid) == 1
    assert again["replayed"] is True and again["cost_usd"] == PICTURE_USD and again["path"] == "assets/door.png"
    assert spend.spend_state(project)["calls"] == 1


@pytest.mark.asyncio
async def test_the_tries_of_a_quote_come_from_the_log_so_an_older_quote_file_gains_none(project, paid, monkeypatch):
    quote = quote_one(project)
    book = (project / ".video-mcp" / "quotes.json").read_text()

    async def refuses(*args, **kwargs):
        raise generate.GenerateError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(generate, "make_picture", refuses)
    for _ in range(2):
        with pytest.raises(spend.SpendError, match="OPENAI_API_KEY"):
            await spend.confirm(project, quote.id)
    (project / ".video-mcp" / "quotes.json").write_text(book)

    with pytest.raises(spend.SpendError, match="2 tries"):
        await spend.confirm(project, quote.id)


@pytest.mark.asyncio
async def test_a_quote_file_that_claims_a_result_is_refused_not_believed(project, paid):
    quote = quote_one(project)

    def forge(quotes):
        quotes[quote.id]["result"] = {"quote_id": quote.id, "kind": "picture", "path": "/etc/hosts", "cost_usd": 0.0}

    edit_book(project, forge)

    with pytest.raises(spend.SpendError, match="cannot be read"):
        await spend.confirm(project, quote.id)
    assert paid == []


# -- every attempt is a call ------------------------------------------------------------------------------------


class Refused(Exception):
    status_code = 400


@pytest.mark.asyncio
async def test_calls_the_provider_refused_still_count_against_the_call_limits(project, paid, tmp_path, monkeypatch):
    limits_file(tmp_path, monkeypatch, "spend:\n  max_calls_per_hour: 3\n")

    async def refuses(*args, **kwargs):
        raise Refused("bad request")

    monkeypatch.setattr(generate, "make_picture", refuses)
    for name in "abc":
        with pytest.raises(spend.SpendError):
            await spend.confirm(project, quote_one(project, name).id)

    with pytest.raises(spend.SpendError, match=r"3 paid calls in the last hour"):
        quote_one(project, "d")
    assert spend.spend_state(project)["spent_usd"] == 0


# -- who decides what was sent ---------------------------------------------------------------------------------


def flagged(error, may_be_billed):
    error.may_be_billed = may_be_billed
    return error


@pytest.mark.parametrize(
    ("error", "released"),
    [
        (generate.GenerateError("no key"), True),
        (Refused("bad"), True),
        (flagged(ContentPolicyError("policy"), False), True),
        (flagged(ContentPolicyError("policy"), True), False),
        (ContentPolicyError("policy"), False),  # nobody said it was not sent
        (flagged(generate.GenerateError("cannot fit the picture"), True), False),  # after the provider answered
        (RuntimeError("dropped"), False),
        (TimeoutError("slow"), False),
    ],
)
def test_a_failure_is_released_only_when_it_is_known_that_nothing_was_billed(error, released):
    assert spend._refused_before_sending(error) is released


@pytest.mark.asyncio
async def test_a_failure_after_the_provider_answered_is_held_not_released(project, paid, monkeypatch):
    quote = quote_one(project)

    async def cannot_fit(*args, **kwargs):
        raise flagged(generate.GenerateError("cannot fit the picture to the canvas"), True)

    monkeypatch.setattr(generate, "make_picture", cannot_fit)

    with pytest.raises(spend.SpendError, match="may have been billed"):
        await spend.confirm(project, quote.id)
    assert spend.spend_state(project)["spent_usd"] == PICTURE_USD


# -- the clock and the lock -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_day_is_read_after_the_wait_for_the_lock_not_before(project, paid, tmp_path, monkeypatch):
    limits_file(tmp_path, monkeypatch, "spend:\n  all_projects_per_day_usd: 0.07\n")
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    clock = {"now": midnight - timedelta(seconds=2)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"]

    @asynccontextmanager
    async def waiting():
        clock["now"] = midnight + timedelta(seconds=3)  # the wait for the lock crossed midnight
        yield

    monkeypatch.setattr(spend, "datetime", Clock)
    quote = quote_one(project)  # made just before midnight
    # another video spent $0.06 just after midnight, while this confirm waited for the lock
    spend._append_spend(
        project,
        {"at": (midnight + timedelta(seconds=1)).isoformat(), "usd": 0.06, "quote": "q-other", "status": "confirmed"},
    )
    monkeypatch.setattr(spend, "_paying", waiting)

    with pytest.raises(spend.SpendError, match="all_projects_per_day_usd"):
        await spend.confirm(project, quote.id)
    assert paid == []


# -- a damaged log ------------------------------------------------------------------------------------------------


def write_line(path, **fields):
    entry = {"at": datetime.now(UTC).isoformat(), "usd": 0.0, "quote": None, "status": "confirmed", **fields}
    text = json.dumps(entry, allow_nan=True)
    with path.open("a") as log:
        log.write(text + "\n")


@pytest.mark.parametrize("usd", [float("nan"), float("inf"), -5.0])
def test_a_spend_line_that_is_not_a_plain_amount_stops_spending_and_says_where(project, usd):
    (project / ".video-mcp").mkdir(exist_ok=True)
    write_line(project / ".video-mcp" / "spend.jsonl", usd=usd)

    with pytest.raises(spend.SpendError, match=r"spend\.jsonl line 1"):
        quote_one(project)


@pytest.mark.asyncio
async def test_a_provider_cost_that_is_not_a_number_is_recorded_at_the_quoted_price(project, paid, monkeypatch):
    quote = quote_one(project)

    async def odd(request, out, **kwargs):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return {"credit": "c", "cost_usd": float("nan")}

    monkeypatch.setattr(generate, "make_picture", odd)

    made = await spend.confirm(project, quote.id)

    assert made["cost_usd"] == PICTURE_USD and "not a usable cost" in made["warning"]
    assert spend.spend_state(project)["spent_usd"] == PICTURE_USD
    quote_one(project, "next")  # the logs are still readable


def test_a_torn_last_line_stops_quoting_but_not_the_person_answering_for_a_hold(project):
    quote = quote_one(project)
    spend._hold(project, quote, PICTURE_USD, "uncertain", "openai", "may have been billed")
    for log in (project / ".video-mcp" / "spend.jsonl", spend.central_log()):
        with log.open("a") as handle:
            handle.write('{"at": "2026-10-10T08:00')  # the disk filled up mid-line

    with pytest.raises(spend.SpendError, match="line"):
        quote_one(project, "other")
    assert [hold["quote"] for hold in spend.holds()] == [quote.id]
    assert spend.clear_hold(quote.id, billed=False)["quote_id"] == quote.id
    assert spend.overview()["damaged_lines"] == 1  # the central log; the project's own is read when it is counted


# -- the switch -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["off", "0", "false", "no", "frozen", "disabled", "stop", "1", "true", "paused", "none", "OFF", " off "]
)
def test_any_value_of_the_variable_but_on_freezes(monkeypatch, value):
    monkeypatch.setenv("HYPERFRAMES_MCP_SPEND", value)

    assert spend.frozen() is not None


@pytest.mark.parametrize("value", ["", "  ", "on", "ON"])
def test_unset_empty_or_on_does_not_freeze(monkeypatch, value):
    monkeypatch.setenv("HYPERFRAMES_MCP_SPEND", value)

    assert spend.frozen() is None


def test_a_freeze_marker_of_any_kind_freezes(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_MCP_HOME", str(tmp_path / "home"))
    marker = spend.user_data_dir() / spend.FREEZE_FILE
    marker.parent.mkdir(parents=True)
    marker.mkdir()  # a folder, not a file

    assert spend.frozen() is not None


@pytest.mark.asyncio
async def test_a_freeze_set_while_the_host_budget_is_checked_still_stops_the_call(project, paid):
    quote = quote_one(project)

    class SlowSink:
        async def check(self, project, manifest, usd):
            spend.freeze()  # the person ran `spend freeze` while this check was waiting

        async def record(self, *args, **kwargs):
            return {}

    spend.use_sink(SlowSink())
    try:
        with pytest.raises(spend.SpendError, match="frozen"):
            await spend.confirm(project, quote.id)
    finally:
        spend.use_sink(None)
        spend.thaw()
    assert paid == []


# -- a paid file is never overwritten ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_quote_for_a_file_that_exists_is_refused_and_so_is_a_confirm_after_it_appeared(project, paid):
    (project / "assets" / "taken.png").write_bytes(b"paid for once")
    with pytest.raises(spend.SpendError, match="already exists"):
        quote_one(project, "taken")

    quote = quote_one(project, "late")
    (project / "assets" / "late.png").write_bytes(b"made in between")
    with pytest.raises(spend.SpendError, match="already exists"):
        await spend.confirm(project, quote.id)
    assert paid == []
    assert not (project / ".video-mcp" / "spend.jsonl").exists() or not spent_lines(project)


# -- the second review ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_quote_filed_under_another_ids_name_is_refused(project, paid):
    quote = quote_one(project)
    await spend.confirm(project, quote.id)
    book = json.loads((project / ".video-mcp" / "quotes.json").read_text())
    entry = book["quotes"][quote.id]
    forged = {**entry, "arguments": {**entry["arguments"], "out": "assets/later.png"}}
    forged["arguments_sha256"] = spend._sha(forged["arguments"])
    book["quotes"]["k0"] = forged  # the same id as the spent quote, filed under another name
    (project / ".video-mcp" / "quotes.json").write_text(json.dumps(book))

    with pytest.raises(spend.SpendError, match="another quote's id"):
        await spend.confirm(project, "k0")

    assert len(paid) == 1


@pytest.mark.asyncio
async def test_the_same_quote_id_in_two_projects_keeps_two_sets_of_lines(project, standalone, paid):
    quote = quote_one(project)
    await spend.confirm(project, quote.id)
    book = (project / ".video-mcp" / "quotes.json").read_text()
    (standalone / ".video-mcp").mkdir()
    other = json.loads(book)
    other["quotes"][quote.id]["arguments"]["out"] = "assets/other.png"
    other["quotes"][quote.id]["arguments"]["profile"] = "default"
    other["quotes"][quote.id]["arguments_sha256"] = spend._sha(other["quotes"][quote.id]["arguments"])
    (standalone / ".video-mcp" / "quotes.json").write_text(json.dumps(other))

    await spend.confirm(standalone, quote.id)  # a forged copy of the id in another folder

    assert spend.spend_state(project)["calls"] == 1 and spend.spend_state(standalone)["calls"] == 1
    assert spend.spend_state(project)["calls_today_all_projects"] == 2


@pytest.mark.asyncio
async def test_a_replay_is_refused_when_the_quote_file_now_names_another_file(project, paid):
    quote = quote_one(project)
    await spend.confirm(project, quote.id)
    (project / "assets" / "other.png").write_bytes(b"something else of the project's")
    book = json.loads((project / ".video-mcp" / "quotes.json").read_text())
    arguments = {**book["quotes"][quote.id]["arguments"], "out": "assets/other.png"}
    book["quotes"][quote.id].update(arguments=arguments, arguments_sha256=spend._sha(arguments))
    (project / ".video-mcp" / "quotes.json").write_text(json.dumps(book))

    with pytest.raises(spend.SpendError, match="is not that file"):
        await spend.confirm(project, quote.id)


@pytest.mark.asyncio
async def test_an_unwritable_own_log_after_the_call_still_tells_the_host_budget_and_keeps_the_central_line(
    project, paid, monkeypatch
):
    quote = quote_one(project)
    told = []

    class Sink:
        async def check(self, project, manifest, usd):
            return None

        async def record(self, project, manifest, usd, note, ref, **kwargs):
            told.append(usd)
            return {"ledger": "ok"}

    real = generate.make_picture

    async def breaks_the_log(*args, **kwargs):
        (project / ".video-mcp" / "spend.jsonl").unlink(missing_ok=True)
        (project / ".video-mcp" / "spend.jsonl").mkdir()  # the agent puts a folder where the log is
        return await real(*args, **kwargs)

    monkeypatch.setattr(generate, "make_picture", breaks_the_log)
    spend.use_sink(Sink())
    try:
        made = await spend.confirm(project, quote.id)
    finally:
        spend.use_sink(None)

    assert told == [PICTURE_USD] and made["recorded"]["sink"] == {"ledger": "ok"}
    assert "own_log_problem" in made["recorded"]
    assert any(line["status"] == "confirmed" for line in spend._log_lines(spend.central_log()))


@pytest.mark.asyncio
async def test_a_reservation_that_cannot_be_written_to_the_project_log_sends_nothing(project, paid):
    quote = quote_one(project)
    (project / ".video-mcp" / "spend.jsonl").mkdir()

    with pytest.raises(spend.SpendError, match="nothing was sent"):
        await spend.confirm(project, quote.id)

    assert paid == []


def test_where_the_logs_disagree_the_central_one_wins_and_a_stale_own_hold_does_not_strand_the_quote(project):
    quote = quote_one(project)
    spend._append_spend(project, {"at": datetime.now(UTC).isoformat(), "usd": PICTURE_USD, "quote": quote.id,
                                  "attempt": 1, "status": "reserved"})  # fmt: skip
    spend._append_line(spend.central_log(), json.dumps({"at": datetime.now(UTC).isoformat(), "usd": 0.0, "quote": quote.id,
                                                        "attempt": 1, "status": "released", "project": str(project)}))  # fmt: skip

    assert spend.spend_state(project)["spent_usd"] == 0 and spend.spend_state(project)["uncertain"] == []
    assert spend._history(project, quote.id)[1]["status"] == "released"


def test_a_log_line_with_a_huge_number_or_an_impossible_time_is_a_problem_not_a_crash(project):
    quote = quote_one(project)
    spend._hold(project, quote, PICTURE_USD, "uncertain", "openai", "x")
    with spend.central_log().open("a") as log:
        log.write('{"at": "2026-10-10T08:00:00+00:00", "usd": ' + "9" * 400 + ', "status": "confirmed"}\n')
        log.write('{"at": "0001-01-01T00:00:00+05:00", "usd": 1.0, "status": "confirmed"}\n')

    assert [hold["quote"] for hold in spend.holds()] == [quote.id]
    assert spend.overview()["damaged_lines"] == 2
    with pytest.raises(spend.SpendError, match="cannot be counted"):
        quote_one(project, "other")


@pytest.mark.asyncio
async def test_a_price_that_is_not_a_number_at_confirm_is_refused_before_anything_is_held(project, paid, monkeypatch):
    quote = quote_one(project)
    monkeypatch.setattr(generate, "picture_price", lambda quality: float("nan"))

    with pytest.raises(spend.SpendError):
        await spend.confirm(project, quote.id)

    assert paid == [] and not (project / ".video-mcp" / "spend.jsonl").exists()


def test_the_answer_for_a_hold_waits_for_a_paid_call_that_is_running(project, monkeypatch):
    import fcntl

    quote = quote_one(project)
    spend._hold(project, quote, PICTURE_USD, "uncertain", "openai", "x")
    monkeypatch.setattr(spend, "CLEAR_WAIT_SECONDS", 0.2)
    lock_path = spend.central_log().with_suffix(".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # a paid call is running

        with pytest.raises(spend.SpendError, match="a paid call is running"):
            spend.clear_hold(quote.id, billed=False)

    assert spend.clear_hold(quote.id, billed=False)["billed"] is False


# -- the third review ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_reservation_that_could_not_be_completed_leaves_no_hold_behind(project, paid):
    quote = quote_one(project)
    (project / ".video-mcp" / "spend.jsonl").mkdir()  # the own log cannot be written

    for _ in range(2):  # each try is released, and counts as one of the quote's tries
        with pytest.raises(spend.SpendError, match="nothing was sent"):
            await spend.confirm(project, quote.id)
    with pytest.raises(spend.SpendError, match="2 tries"):
        await spend.confirm(project, quote.id)

    assert paid == [] and spend.holds() == []
    assert spend.spend_state(project)["spent_usd"] == 0


@pytest.mark.asyncio
async def test_a_central_log_that_cannot_be_written_after_the_call_still_tells_the_host_budget(project, paid, monkeypatch):
    quote = quote_one(project)
    told = []

    class Sink:
        async def check(self, project, manifest, usd):
            return None

        async def record(self, project, manifest, usd, note, ref, **kwargs):
            told.append(usd)
            return {}

    real = spend._append_line

    def central_breaks_after_the_call(path, text):
        if path == spend.central_log() and '"confirmed"' in text:
            raise PermissionError("read-only")
        return real(path, text)

    monkeypatch.setattr(spend, "_append_line", central_breaks_after_the_call)
    spend.use_sink(Sink())
    try:
        made = await spend.confirm(project, quote.id)
    finally:
        spend.use_sink(None)

    assert told == [PICTURE_USD] and "central spend log could not be written" in made["recorded"]["own_log_problem"]
    assert paid and spend.spend_state(project)["spent_usd"] == PICTURE_USD  # held in the central log, spent in the own


@pytest.mark.asyncio
async def test_a_renamed_project_keeps_its_holds_and_the_answer_for_them(project, paid, tmp_path):
    quote = quote_one(project)
    kill_after_sending(project, quote.id)
    moved = tmp_path / "renamed"
    project.rename(moved)

    assert [h["quote_id"] for h in spend.spend_state(moved)["uncertain"]] == [quote.id]
    spend.clear_hold(quote.id, billed=False)  # written for the old path, found again by the folder's identity

    assert spend.spend_state(moved)["uncertain"] == [] and spend.spend_state(moved)["spent_usd"] == 0


@pytest.mark.asyncio
async def test_another_spelling_of_the_path_is_the_same_project(project, paid):
    spelled = project.parent / project.name.upper()
    if not spelled.exists():
        pytest.skip("this disk tells upper and lower case apart")
    quote = quote_one(project)
    await spend.confirm(project, quote.id)
    (project / ".video-mcp" / "spend.jsonl").unlink()
    (project / "assets" / "door.png").unlink()

    with pytest.raises(spend.SpendError, match=r"already (made|billed)"):
        await spend.confirm(spelled, quote.id)
    assert len(paid) == 1


@pytest.mark.asyncio
async def test_a_call_the_person_cleared_as_billed_has_no_file_to_hand_back(project, paid):
    quote = quote_one(project)
    kill_after_sending(project, quote.id)
    spend.clear_hold(quote.id, billed=True)
    (project / "assets" / "door.png").write_bytes(b"the agent put this here")

    with pytest.raises(spend.SpendError, match="no file was recorded"):
        await spend.confirm(project, quote.id)
