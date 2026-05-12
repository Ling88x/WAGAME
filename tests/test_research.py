"""Tests for the Research system — node catalog, plan/prereq, DB flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from wagame.cogs.research import (
    claim_finished_research,
    start_research,
)
from wagame.db import Database
from wagame.game.research import (
    format_duration,
    next_level,
    plan_research,
    prereq_met,
)
from wagame.research_data import NODES, NODES_BY_CODENAME, get_node


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "research.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


# -- catalog ----------------------------------------------------------------


def test_every_node_has_consistent_cost_and_duration_lengths() -> None:
    for node in NODES:
        assert len(node.costs) == node.max_level, node.codename
        assert len(node.durations) == node.max_level, node.codename
        assert node.max_level >= 1


def test_prereqs_point_at_existing_nodes() -> None:
    for node in NODES:
        if node.requires is not None:
            assert node.requires in NODES_BY_CODENAME, node.codename
            assert node.requires_level >= 1
            assert node.requires_level <= NODES_BY_CODENAME[node.requires].max_level


def test_effect_columns_are_whitelisted() -> None:
    from wagame.cogs.research import ALLOWED_EFFECT_COLUMNS

    for node in NODES:
        assert node.effect_column in ALLOWED_EFFECT_COLUMNS, node.codename


# -- pure logic -------------------------------------------------------------


def test_next_level_walks_up_and_stops_at_max() -> None:
    node = get_node("gather_yield")
    assert node is not None
    assert next_level(0, node) == 1
    assert next_level(node.max_level - 1, node) == node.max_level
    assert next_level(node.max_level, node) is None


def test_prereq_met_for_unrestricted_node() -> None:
    node = get_node("gather_yield")
    assert node is not None
    assert prereq_met(node, prereq_current_level=0)


def test_prereq_met_for_gated_node() -> None:
    node = get_node("training_speed")
    assert node is not None
    # Requires training_queue level 2.
    assert not prereq_met(node, prereq_current_level=1)
    assert prereq_met(node, prereq_current_level=2)
    assert prereq_met(node, prereq_current_level=5)


def test_plan_research_returns_cost_and_time() -> None:
    node = get_node("gather_yield")
    assert node is not None
    plan = plan_research(node, target_level=1)
    assert plan.gold_cost == node.costs[0]
    assert plan.total_seconds == node.durations[0]


def test_format_duration_units() -> None:
    assert format_duration(0) == ""
    assert format_duration(45) == "45s"
    assert format_duration(125) == "2m 05s"
    assert format_duration(3725) == "1h 02m"
    assert format_duration(90061) == "1d 01h"


# -- start_research ---------------------------------------------------------


async def test_start_research_charges_gold_and_creates_job(db: Database) -> None:
    user_id = 1
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET gold = 5000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()

    ok, _ = await start_research(db, user_id, "gather_yield")
    assert ok

    async with db.conn.execute(
        "SELECT gold FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["gold"] == 5000 - 500  # gather_yield lvl 1 costs 500

    async with db.conn.execute(
        "SELECT node_codename, target_level FROM research_jobs WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        job = await cur.fetchone()
    assert job is not None
    assert job["node_codename"] == "gather_yield"
    assert job["target_level"] == 1


async def test_start_research_rejects_when_short_on_gold(db: Database) -> None:
    user_id = 2
    await db.get_or_create_player(user_id)
    ok, msg = await start_research(db, user_id, "gather_yield")
    assert not ok
    assert "gold" in msg.lower()


async def test_start_research_rejects_concurrent_job(db: Database) -> None:
    user_id = 3
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET gold = 100000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()
    assert (await start_research(db, user_id, "gather_yield"))[0]
    ok, msg = await start_research(db, user_id, "gather_speed")
    assert not ok
    assert "already" in msg.lower()


async def test_start_research_blocks_locked_prereq(db: Database) -> None:
    user_id = 4
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET gold = 100000 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()
    # training_speed requires training_queue lvl 2.
    ok, msg = await start_research(db, user_id, "training_speed")
    assert not ok
    assert "requires" in msg.lower()


async def test_start_research_rejects_maxed_node(db: Database) -> None:
    user_id = 5
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET gold = 1000000 WHERE discord_user_id = ?", (user_id,)
    )
    node = get_node("gather_yield")
    assert node is not None
    await db.conn.execute(
        "INSERT INTO player_research (discord_user_id, node_codename, level) VALUES (?, ?, ?)",
        (user_id, node.codename, node.max_level),
    )
    await db.conn.commit()
    ok, msg = await start_research(db, user_id, "gather_yield")
    assert not ok
    assert "maxed" in msg.lower()


# -- claim_finished_research -----------------------------------------------


async def test_claim_finished_applies_effect_and_bumps_level(db: Database) -> None:
    user_id = 6
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO research_jobs (discord_user_id, node_codename, target_level,
                                   started_at, finishes_at)
        VALUES (?, 'gather_yield', 1, 0, 1)
        """,
        (user_id,),
    )
    await db.conn.commit()

    claimed = await claim_finished_research(db, user_id)
    assert claimed == ("Gather Yield", 1)

    async with db.conn.execute(
        "SELECT gather_yield_pct FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["gather_yield_pct"] == 5  # +5% per level


async def test_claim_unlock_tier_research_lifts_player(db: Database) -> None:
    user_id = 7
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO research_jobs (discord_user_id, node_codename, target_level,
                                   started_at, finishes_at)
        VALUES (?, 'troop_tier', 1, 0, 1)
        """,
        (user_id,),
    )
    await db.conn.commit()

    await claim_finished_research(db, user_id)

    async with db.conn.execute(
        "SELECT unlocked_tier FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["unlocked_tier"] == 2  # 1 (default) + 1


async def test_claim_march_capacity_research_lifts_player(db: Database) -> None:
    user_id = 8
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO research_jobs (discord_user_id, node_codename, target_level,
                                   started_at, finishes_at)
        VALUES (?, 'march_capacity', 1, 0, 1)
        """,
        (user_id,),
    )
    await db.conn.commit()

    await claim_finished_research(db, user_id)

    async with db.conn.execute(
        "SELECT march_capacity FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["march_capacity"] == 3  # 2 (default) + 1


async def test_claim_noop_when_no_job(db: Database) -> None:
    user_id = 9
    await db.get_or_create_player(user_id)
    assert await claim_finished_research(db, user_id) is None


async def test_claim_skips_unfinished_job(db: Database) -> None:
    user_id = 10
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        """
        INSERT INTO research_jobs (discord_user_id, node_codename, target_level,
                                   started_at, finishes_at)
        VALUES (?, 'gather_yield', 1, 0, 9999999999)
        """,
        (user_id,),
    )
    await db.conn.commit()
    assert await claim_finished_research(db, user_id) is None


# -- gather bonuses ---------------------------------------------------------


async def test_gather_yield_pct_is_applied_at_start(db: Database) -> None:
    from wagame.cogs.gather import _start_gather

    user_id = 11
    await db.get_or_create_player(user_id)
    # Make yield perfectly deterministic: 100% bonus -> base x2.
    await db.conn.execute(
        "UPDATE players SET gather_yield_pct = 100 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()

    ok, _ = await _start_gather(db, user_id, "food")
    assert ok

    async with db.conn.execute(
        "SELECT yield_amount FROM marches WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    # Base food range is 500-1000, doubled -> 1000-2000.
    assert 1000 <= int(row["yield_amount"]) <= 2000


async def test_gather_speed_pct_shortens_duration(db: Database) -> None:
    from wagame.cogs.gather import _start_gather
    from wagame.game.gather import GATHER_DURATION_SECONDS

    user_id = 12
    await db.get_or_create_player(user_id)
    # 50% speed: duration halved.
    await db.conn.execute(
        "UPDATE players SET gather_speed_pct = 50 WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()

    ok, _ = await _start_gather(db, user_id, "wood")
    assert ok

    async with db.conn.execute(
        "SELECT started_at, finishes_at FROM marches WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    duration = int(row["finishes_at"]) - int(row["started_at"])
    # Half of 1800s = 900s; allow a few seconds of leeway for rounding.
    assert duration == GATHER_DURATION_SECONDS // 2
