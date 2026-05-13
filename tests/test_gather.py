"""Tests for the gathering system — pure logic plus DB-backed start/claim."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from wagame.cogs.gather import _claim_ready, _start_gather
from wagame.db import Database
from wagame.game.gather import (
    BASE_YIELD,
    CRIT_MULTIPLIER,
    DEFAULT_SLOTS,
    MAX_SLOTS,
    format_remaining,
    is_finished,
    remaining_seconds,
    roll_gather,
)
from wagame.ui import Outcome


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "gather.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


def test_default_slots_below_cap() -> None:
    assert 1 <= DEFAULT_SLOTS <= MAX_SLOTS


def test_gold_base_yield_is_leaner_than_food_and_wood() -> None:
    gold_lo, gold_hi = BASE_YIELD["gold"]
    food_lo, food_hi = BASE_YIELD["food"]
    wood_lo, wood_hi = BASE_YIELD["wood"]
    assert gold_hi < food_hi
    assert gold_hi < wood_hi
    assert gold_lo <= food_lo
    assert gold_lo <= wood_lo


def test_roll_gather_clean() -> None:
    rng = random.Random(0)
    roll = roll_gather("food", rng)
    lo, hi = BASE_YIELD["food"]
    assert lo <= roll.base <= hi
    assert roll.total == (roll.base * CRIT_MULTIPLIER if roll.crit else roll.base)


def test_roll_gather_rejects_unknown_resource() -> None:
    with pytest.raises(ValueError):
        roll_gather("mythril")  # type: ignore[arg-type]


def test_remaining_and_is_finished() -> None:
    assert remaining_seconds(100, 80) == 20
    assert remaining_seconds(100, 200) == 0
    assert is_finished(100, 100)
    assert is_finished(100, 150)
    assert not is_finished(100, 99)


def test_format_remaining_units() -> None:
    assert format_remaining(0) == ""
    assert format_remaining(45) == "45s"
    assert format_remaining(125) == "2m 05s"
    assert format_remaining(3725) == "1h 02m"


async def test_start_gather_persists_row(db: Database) -> None:
    await db.get_or_create_player(1)
    result = await _start_gather(db, 1, "food")
    assert result.outcome == Outcome.SUCCESS
    async with db.conn.execute(
        "SELECT resource, yield_amount, crit FROM marches WHERE discord_user_id = 1"
    ) as cur:
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["resource"] == "food"
    lo, hi = BASE_YIELD["food"]
    assert lo <= rows[0]["yield_amount"] <= hi
    assert rows[0]["crit"] in (0, 1)


async def test_start_gather_respects_capacity(db: Database) -> None:
    await db.get_or_create_player(2)  # capacity = 2 by default
    for _ in range(2):
        result = await _start_gather(db, 2, "wood")
        assert result.outcome == Outcome.SUCCESS
    result = await _start_gather(db, 2, "wood")
    assert result.outcome == Outcome.ERROR
    assert "busy" in result.message.lower()


async def test_claim_ready_credits_resources_and_clears_rows(db: Database) -> None:
    await db.get_or_create_player(3)
    # Insert a finished march directly to avoid waiting on the real clock.
    await db.conn.execute(
        """
        INSERT INTO marches (discord_user_id, resource, started_at, finishes_at,
                             yield_amount, crit)
        VALUES (3, 'gold', 0, 1, 500, 0)
        """
    )
    await db.conn.commit()

    count, totals, crits = await _claim_ready(db, 3)
    assert count == 1
    assert totals["gold"] == 500
    assert crits == 0

    async with db.conn.execute("SELECT gold FROM players WHERE discord_user_id = 3") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["gold"] == 500

    async with db.conn.execute(
        "SELECT COUNT(*) FROM marches WHERE discord_user_id = 3"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_claim_ready_applies_crit_multiplier(db: Database) -> None:
    await db.get_or_create_player(4)
    await db.conn.execute(
        """
        INSERT INTO marches (discord_user_id, resource, started_at, finishes_at,
                             yield_amount, crit)
        VALUES (4, 'wood', 0, 1, 700, 1)
        """
    )
    await db.conn.commit()

    count, totals, crits = await _claim_ready(db, 4)
    assert count == 1
    assert crits == 1
    assert totals["wood"] == 700 * CRIT_MULTIPLIER


async def test_claim_ready_skips_unfinished_rows(db: Database) -> None:
    await db.get_or_create_player(5)
    # finishes_at far in the future
    await db.conn.execute(
        """
        INSERT INTO marches (discord_user_id, resource, started_at, finishes_at,
                             yield_amount, crit)
        VALUES (5, 'food', 0, 9999999999, 500, 0)
        """
    )
    await db.conn.commit()

    count, totals, crits = await _claim_ready(db, 5)
    assert count == 0
    assert totals == {}
    assert crits == 0
