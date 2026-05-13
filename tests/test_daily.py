"""Tests for the daily +100 gem auto-claim helper."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from wagame.db import Database
from wagame.game.daily import (
    DAILY_GEM_BONUS,
    RESET_HOUR_UTC,
    claim_daily_if_due,
    current_reset_day,
)
from wagame.game.gacha import STARTING_GEMS


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "daily.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


# -- reset-day arithmetic -------------------------------------------------


def test_reset_day_before_boundary_returns_previous_calendar_day() -> None:
    # 20:00 UTC on 2026-05-13 is still in the reset day that began at
    # 21:00 UTC on 2026-05-12.
    moment = datetime.datetime(2026, 5, 13, 20, 0, tzinfo=datetime.UTC)
    assert current_reset_day(moment) == datetime.date(2026, 5, 12)


def test_reset_day_at_boundary_returns_same_calendar_day() -> None:
    # 21:00 UTC is the exact start of a new reset day.
    moment = datetime.datetime(2026, 5, 13, RESET_HOUR_UTC, 0, tzinfo=datetime.UTC)
    assert current_reset_day(moment) == datetime.date(2026, 5, 13)


def test_reset_day_assumes_utc_when_naive() -> None:
    naive = datetime.datetime(2026, 5, 13, 22, 0)
    assert current_reset_day(naive) == datetime.date(2026, 5, 13)


# -- idempotent claim -----------------------------------------------------


async def test_first_call_after_join_grants_bonus_via_get_or_create(db: Database) -> None:
    # get_or_create_player triggers an inline claim. New player starts with
    # STARTING_GEMS + the day's daily bonus.
    player = await db.get_or_create_player(1)
    assert int(player["gems"]) == STARTING_GEMS + DAILY_GEM_BONUS
    assert player["last_daily_claim_date"] is not None


async def test_second_call_same_day_is_no_op(db: Database) -> None:
    await db.get_or_create_player(2)
    before = await db.get_or_create_player(2)  # triggers another claim attempt
    granted = await claim_daily_if_due(db, 2)
    assert granted == 0
    after = await db.get_or_create_player(2)
    assert int(after["gems"]) == int(before["gems"])


async def test_new_reset_day_grants_again(db: Database) -> None:
    await db.get_or_create_player(3)
    # Force the stored date back by one day to simulate a reset.
    yesterday = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    await db.conn.execute(
        "UPDATE players SET last_daily_claim_date = ? WHERE discord_user_id = 3",
        (yesterday,),
    )
    await db.conn.commit()
    granted = await claim_daily_if_due(db, 3)
    assert granted == DAILY_GEM_BONUS


async def test_claim_on_missing_player_is_noop(db: Database) -> None:
    granted = await claim_daily_if_due(db, 9999)
    assert granted == 0
