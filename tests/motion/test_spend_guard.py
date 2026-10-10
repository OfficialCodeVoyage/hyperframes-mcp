"""The overspend guard beyond the limits: money is held before a call is sent, a call that may have been billed keeps
counting, a loop is stopped by the number of calls whatever the prices say, a variable can only tighten a limit, and one
switch freezes every paid call."""

from __future__ import annotations

import asyncio
import json

import pytest

from hyperframes_mcp.motion import generate, spend
from tests.motion.spend_helpers import PICTURE_USD, earlier_spend, spent_lines


def quote_one(project, name="door"):
    return spend.quote_picture(project, "x", quality="high", out=f"assets/{name}.png")


def limits_file(tmp_path, monkeypatch, text: str):
    path = tmp_path / "limits.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(spend, "CONFIG_PATH", path)
    return path


# -- the hold before the call ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_price_is_held_before_the_provider_is_called_and_counted_once_after(project, paid, monkeypatch):
    quote = quote_one(project)
    during = {}
    real = generate.make_picture

    async def watching(*args, **kwargs):
        during["spent"] = spend.spend_state(project)["spent_usd"]
        during["statuses"] = [line["status"] for line in spent_lines(project)]
        return await real(*args, **kwargs)

    monkeypatch.setattr(generate, "make_picture", watching)

    await spend.confirm(project, quote.id)

    assert during == {"spent": PICTURE_USD, "statuses": ["reserved"]}
    state = spend.spend_state(project)
    assert state["spent_usd"] == PICTURE_USD and state["calls"] == 1  # the hold became the spend, not a second one
    assert state["uncertain"] == []


@pytest.mark.asyncio
async def test_a_hold_left_by_a_process_that_died_counts_against_the_limit(project, paid):
    spend._append_spend(
        project,
        {"at": "2026-10-10T08:00:00+00:00", "usd": 9.99, "quote": "q-dead", "status": "reserved", "note": "killed"},
    )

    with pytest.raises(spend.SpendError, match="per_project_usd"):
        quote_one(project)
    assert [hold["quote_id"] for hold in spend.spend_state(project)["uncertain"]] == ["q-dead"]


# -- a call that may have been billed ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_call_that_failed_after_it_was_sent_may_have_been_billed_and_keeps_counting(project, paid, monkeypatch):
    quote = quote_one(project)

    async def drops(*args, **kwargs):
        raise RuntimeError("the connection dropped")

    monkeypatch.setattr(generate, "make_picture", drops)

    with pytest.raises(spend.SpendError, match="may have been billed"):
        await spend.confirm(project, quote.id)

    state = spend.spend_state(project)
    assert state["spent_usd"] == PICTURE_USD
    assert [hold["quote_id"] for hold in state["uncertain"]] == [quote.id]
    with pytest.raises(spend.SpendError, match="may have been billed.*spend clear"):
        await spend.confirm(project, quote.id)  # never the same quote again
    assert paid == []


@pytest.mark.asyncio
async def test_a_cancelled_call_keeps_its_hold(project, paid, monkeypatch):
    quote = quote_one(project)

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(generate, "make_picture", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await spend.confirm(project, quote.id)

    assert spend.spend_state(project)["spent_usd"] == PICTURE_USD
    assert len(spend.spend_state(project)["uncertain"]) == 1


@pytest.mark.asyncio
async def test_a_refusal_before_anything_was_sent_releases_the_hold_and_a_quote_gets_two_tries(
    project, paid, monkeypatch
):
    quote = quote_one(project)

    async def refuses(*args, **kwargs):
        raise generate.GenerateError("OPENAI_API_KEY is not set")

    monkeypatch.setattr(generate, "make_picture", refuses)
    for _ in range(2):
        with pytest.raises(spend.SpendError, match="OPENAI_API_KEY"):
            await spend.confirm(project, quote.id)
    assert spend.spend_state(project)["spent_usd"] == 0

    with pytest.raises(spend.SpendError, match="2 tries"):
        await spend.confirm(project, quote.id)


@pytest.mark.asyncio
async def test_clearing_a_hold_after_checking_the_provider_frees_it_or_makes_it_a_spend(project, paid, monkeypatch):
    async def drops(*args, **kwargs):
        raise RuntimeError("the connection dropped")

    monkeypatch.setattr(generate, "make_picture", drops)
    first, second = quote_one(project, "a"), quote_one(project, "b")
    for quote in (first, second):
        with pytest.raises(spend.SpendError):
            await spend.confirm(project, quote.id)
    assert spend.spend_state(project)["spent_usd"] == pytest.approx(2 * PICTURE_USD)

    assert spend.clear_hold(first.id, billed=False)["project"] == str(project.resolve())
    assert spend.spend_state(project)["spent_usd"] == pytest.approx(PICTURE_USD)
    spend.clear_hold(second.id, billed=True)

    state = spend.spend_state(project)
    assert state["spent_usd"] == pytest.approx(PICTURE_USD) and state["uncertain"] == []
    with pytest.raises(spend.SpendError, match="no hold"):
        spend.clear_hold("q-nope", billed=False)


# -- the number of calls --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_calls_in_an_hour_are_counted_apart_from_their_prices(project, paid, tmp_path, monkeypatch):
    limits_file(tmp_path, monkeypatch, "spend:\n  max_calls_per_hour: 2\n")
    for name in ("a", "b"):
        await spend.confirm(project, quote_one(project, name).id)

    with pytest.raises(spend.SpendError, match=r"2 paid calls in the last hour.*max_calls_per_hour"):
        quote_one(project, "c")
    assert len(paid) == 2


@pytest.mark.asyncio
async def test_the_calls_in_a_day_are_counted_over_every_project(project, standalone, paid, tmp_path, monkeypatch):
    limits_file(tmp_path, monkeypatch, "spend:\n  max_calls_per_day: 2\n")
    await spend.confirm(project, quote_one(project, "a").id)
    await spend.confirm(standalone, quote_one(standalone, "b").id)

    with pytest.raises(spend.SpendError, match=r"2 paid calls today.*max_calls_per_day"):
        quote_one(project, "c")


def test_a_spend_noted_by_hand_is_not_a_call(project, tmp_path, monkeypatch):
    limits_file(tmp_path, monkeypatch, "spend:\n  max_calls_per_hour: 1\n")
    earlier_spend(project, 0.5)

    quote_one(project)  # a line without a quote is a note, not a call of the server


# -- the variables ---------------------------------------------------------------------------------------------


def test_a_variable_can_lower_a_limit_and_never_raise_it(monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_PROJECT_USD", "3")
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_CALL_USD", "99")
    monkeypatch.setenv("HYPERFRAMES_MCP_MAX_CALLS_PER_HOUR", "4")

    limits = spend.load_spend_limits()

    assert (limits.per_project_usd, limits.per_call_usd, limits.max_calls_per_hour) == (3.0, 1.0, 4)


def test_a_variable_sets_a_limit_the_file_leaves_open(monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_DAY_USD", "2.5")

    assert spend.load_spend_limits().per_day_usd == 2.5


def test_a_variable_that_is_not_a_positive_number_is_an_error_in_words(monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_CALL_USD", "lots")

    with pytest.raises(ValueError, match="HYPERFRAMES_MCP_PER_CALL_USD"):
        spend.load_spend_limits()


def test_the_state_names_the_variables_in_force(project, monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_MCP_PER_PROJECT_USD", "3")

    state = spend.spend_state(project)

    assert state["limits"]["per_project_usd"] == 3.0
    assert state["variables_in_force"] == ["HYPERFRAMES_MCP_PER_PROJECT_USD"]


# -- the switch ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_frozen_server_still_quotes_and_pays_nothing(project, paid):
    quote = quote_one(project)
    spend.freeze()

    assert spend.spend_state(project)["frozen"]
    with pytest.raises(spend.SpendError, match="frozen.*spend thaw"):
        await spend.confirm(project, quote.id)
    quote_one(project, "other")  # a price can still be asked
    spend.thaw()

    await spend.confirm(project, quote.id)
    assert len(paid) == 1 and spend.spend_state(project)["frozen"] is None


@pytest.mark.asyncio
async def test_the_variable_freezes_too(project, paid, monkeypatch):
    quote = quote_one(project)
    monkeypatch.setenv("HYPERFRAMES_MCP_SPEND", "off")

    with pytest.raises(spend.SpendError, match="HYPERFRAMES_MCP_SPEND"):
        await spend.confirm(project, quote.id)
    assert paid == []


@pytest.mark.asyncio
async def test_a_cost_above_the_quote_is_recorded_in_full_and_said(project, paid, monkeypatch):
    quote = quote_one(project)

    async def dearer(request, out, **kwargs):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x")
        return {"credit": "c", "cost_usd": PICTURE_USD + 0.2}

    monkeypatch.setattr(generate, "make_picture", dearer)

    made = await spend.confirm(project, quote.id)

    assert made["cost_usd"] == pytest.approx(PICTURE_USD + 0.2)
    assert "above its quote" in made["warning"]
    assert spend.spend_state(project)["spent_usd"] == pytest.approx(PICTURE_USD + 0.2)
    assert json.loads(json.dumps(made))  # plain data
