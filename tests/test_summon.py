"""Tests for the Summoning Gate — pure logic + DB-backed train/claim flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from wagame.cogs.summon import claim_finished_training, start_training
from wagame.db import Database
from wagame.game.summon import (
    food_cost,
    format_duration,
    max_affordable_count,
    plan_training,
    total_train_seconds,
)
from wagame.troops_data import TROOPS, sync_troops


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "summon.db")
    await database.connect()
    await database.migrate()
    await sync_troops(database)
    yield database
    await database.close()


# -- pure logic -------------------------------------------------------------


def test_food_cost_linear() -> None:
    assert food_cost(15, 10) == 150
    assert food_cost(0, 1000) == 0
    assert food_cost(50, 0) == 0


def test_total_train_seconds_applies_boost() -> None:
    assert total_train_seconds(30, 10, 0) == 300
    # 10% boost on 300s → 270s
    assert total_train_seconds(30, 10, 10) == 270
    # 99% cap: never goes below 1s for non-zero base
    assert total_train_seconds(30, 10, 999) == 3


def test_total_train_seconds_zero_count() -> None:
    assert total_train_seconds(30, 0, 50) == 0


def test_plan_training_packages_everything() -> None:
    plan = plan_training(
        troop_codename="catsith",
        count=10,
        food_per_unit=15,
        train_seconds_per_unit=30,
        speed_boost_pct=0,
    )
    assert plan.troop_codename == "catsith"
    assert plan.count == 10
    assert plan.food_cost == 150
    assert plan.total_seconds == 300


def test_plan_training_rejects_nonpositive_count() -> None:
    with pytest.raises(ValueError):
        plan_training(
            troop_codename="catsith",
            count=0,
            food_per_unit=15,
            train_seconds_per_unit=30,
            speed_boost_pct=0,
        )


def test_max_affordable_count_limited_by_food_or_cap() -> None:
    assert max_affordable_count(food_available=300, food_per_unit=15, queue_cap=50) == 20
    assert max_affordable_count(food_available=10_000, food_per_unit=15, queue_cap=50) == 50
    assert max_affordable_count(food_available=0, food_per_unit=15, queue_cap=50) == 0
    # Free troops still capped at queue.
    assert max_affordable_count(food_available=100, food_per_unit=0, queue_cap=50) == 50


def test_format_duration_units() -> None:
    assert format_duration(0) == ""
    assert format_duration(45) == "45s"
    assert format_duration(125) == "2m 05s"
    assert format_duration(3725) == "1h 02m"
    assert format_duration(90061) == "1d 01h"


# -- catalog ----------------------------------------------------------------


def test_troop_catalog_has_eight_units_across_four_tiers() -> None:
    assert len(TROOPS) == 8
    tiers = sorted({t.tier for t in TROOPS})
    assert tiers == [1, 2, 3, 4]
    # T3 and T4 must have three role variants each.
    for tier in (3, 4):
        roles = {t.role for t in TROOPS if t.tier == tier}
        assert roles == {"monster", "defender", "player"}


async def test_sync_troops_is_idempotent(db: Database) -> None:
    await sync_troops(db)  # second time
    async with db.conn.execute("SELECT COUNT(*) FROM troops") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 8


# -- start_training --------------------------------------------------------


async def test_start_training_succeeds_for_t1_with_food(db: Database) -> None:
    user_id = 1
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET food = 10000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()

    ok, msg = await start_training(db, user_id, "catsith", 10)
    assert ok, msg

    async with db.conn.execute(
        "SELECT food FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["food"] == 10000 - 15 * 10

    async with db.conn.execute(
        "SELECT troop_codename, count FROM training_jobs WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        job = await cur.fetchone()
    assert job is not None
    assert job["troop_codename"] == "catsith"
    assert job["count"] == 10


async def test_start_training_blocks_locked_tier(db: Database) -> None:
    user_id = 2
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET food = 1000000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()

    ok, msg = await start_training(db, user_id, "gryphon", 1)
    assert not ok
    assert "locked" in msg.lower()


async def test_start_training_blocks_when_food_short(db: Database) -> None:
    user_id = 3
    await db.get_or_create_player(user_id)
    # Default food is 0, T1 costs 15/unit.
    ok, msg = await start_training(db, user_id, "catsith", 5)
    assert not ok
    assert "food" in msg.lower()


async def test_start_training_blocks_above_queue_cap(db: Database) -> None:
    user_id = 4
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET food = 10000000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()  # default queue cap = 50
    ok, msg = await start_training(db, user_id, "catsith", 51)
    assert not ok
    assert "cap" in msg.lower()


async def test_start_training_rejects_second_concurrent_job(db: Database) -> None:
    user_id = 5
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET food = 10000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()
    assert (await start_training(db, user_id, "catsith", 1))[0]
    ok, msg = await start_training(db, user_id, "catsith", 1)
    assert not ok
    assert "already" in msg.lower()


# -- claim_finished_training ------------------------------------------------


async def test_claim_finished_moves_troops_and_clears_job(db: Database) -> None:
    user_id = 6
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO training_jobs (discord_user_id, troop_codename, count,
                                   started_at, finishes_at)
        VALUES (?, 'catsith', 25, 0, 1)
        """,
        (user_id,),
    )
    await db.conn.commit()

    claimed = await claim_finished_training(db, user_id)
    assert claimed == ("Catsith", 25)

    async with db.conn.execute(
        "SELECT count FROM owned_troops WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["count"] == 25

    async with db.conn.execute(
        "SELECT COUNT(*) FROM training_jobs WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_claim_finished_stacks_on_existing_count(db: Database) -> None:
    user_id = 7
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO owned_troops (discord_user_id, troop_codename, count)
        VALUES (?, 'catsith', 10)
        """,
        (user_id,),
    )
    await db.conn.execute(
        """
        INSERT INTO training_jobs (discord_user_id, troop_codename, count,
                                   started_at, finishes_at)
        VALUES (?, 'catsith', 5, 0, 1)
        """,
        (user_id,),
    )
    await db.conn.commit()

    await claim_finished_training(db, user_id)
    async with db.conn.execute(
        "SELECT count FROM owned_troops WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["count"] == 15


async def test_claim_skips_unfinished_job(db: Database) -> None:
    user_id = 8
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO training_jobs (discord_user_id, troop_codename, count,
                                   started_at, finishes_at)
        VALUES (?, 'catsith', 10, 0, 9999999999)
        """,
        (user_id,),
    )
    await db.conn.commit()
    assert await claim_finished_training(db, user_id) is None
    async with db.conn.execute(
        "SELECT COUNT(*) FROM training_jobs WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_claim_is_a_noop_when_no_job(db: Database) -> None:
    user_id = 9
    await db.get_or_create_player(user_id)
    assert await claim_finished_training(db, user_id) is None
