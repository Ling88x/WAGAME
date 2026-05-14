"""DB-backed tests for the /wa hub render — status lines reflect state."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wagame.cogs.hub import (
    _gather_line,
    _hub_state,
    _hunt_line,
    _quota_line,
    _research_line,
    _train_line,
)
from wagame.db import Database
from wagame.game.daily import current_reset_day
from wagame.heroes_data import sync_heroes
from wagame.troops_data import sync_troops


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "hub.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    await sync_troops(database)
    yield database
    await database.close()


# -- empty state ---------------------------------------------------------


async def test_hub_state_for_new_player_is_all_idle(db: Database) -> None:
    await db.get_or_create_player(1)
    state = await _hub_state(db, 1)
    assert state["gather_count"] == 0
    assert state["research_job"] is None
    assert state["train_job"] is None
    assert state["hunt_march"] is None
    assert state["daily_kills"] == 0
    assert state["heroes_count"] == 0
    assert _gather_line(state) == "Idle"
    assert _research_line(state) == "Idle"
    assert _train_line(state) == "Idle"
    assert _hunt_line(state) == "Idle"


# -- gather line ---------------------------------------------------------


async def test_gather_line_reflects_in_flight(db: Database) -> None:
    await db.get_or_create_player(1)
    future = int(time.time()) + 1000
    await db.conn.execute(
        "INSERT INTO marches (discord_user_id, resource, started_at, finishes_at, "
        "yield_amount, crit) VALUES (?, 'gold', 0, ?, 500, 0)",
        (1, future),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    assert "claim <t:" in _gather_line(state)


async def test_gather_line_flags_ready_to_claim(db: Database) -> None:
    await db.get_or_create_player(1)
    past = int(time.time()) - 100
    await db.conn.execute(
        "INSERT INTO marches (discord_user_id, resource, started_at, finishes_at, "
        "yield_amount, crit) VALUES (?, 'gold', 0, ?, 500, 0)",
        (1, past),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _gather_line(state)
    assert "ready to claim" in line


# -- training line -------------------------------------------------------


async def test_train_line_includes_troop_name_and_count(db: Database) -> None:
    await db.get_or_create_player(1)
    future = int(time.time()) + 600
    await db.conn.execute(
        "INSERT INTO training_jobs (discord_user_id, troop_codename, count, "
        "started_at, finishes_at) VALUES (?, 'catsith', 50, 0, ?)",
        (1, future),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _train_line(state)
    assert "50x Catsith" in line
    assert "<t:" in line


# -- research line -------------------------------------------------------


async def test_research_line_includes_node_name(db: Database) -> None:
    await db.get_or_create_player(1)
    future = int(time.time()) + 600
    await db.conn.execute(
        "INSERT INTO research_jobs (discord_user_id, node_codename, target_level, "
        "started_at, finishes_at) VALUES (?, 'gather_yield', 2, 0, ?)",
        (1, future),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _research_line(state)
    assert "Lv 2" in line


# -- hunt line -----------------------------------------------------------


async def test_hunt_line_outbound_shows_engagement_timestamp(db: Database) -> None:
    await db.get_or_create_player(1)
    started = int(time.time())
    completes = started + 60
    await db.conn.execute(
        "INSERT INTO hunt_marches (discord_user_id, level, hero_id, started_at, "
        "completes_at, damage) VALUES (?, 3, NULL, ?, ?, 1000)",
        (1, started, completes),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _hunt_line(state)
    assert "Marching" in line
    assert "engages <t:" in line


async def test_hunt_line_return_shows_home_timestamp(db: Database) -> None:
    await db.get_or_create_player(1)
    started = int(time.time())
    completes = started + 60
    engaged_at = started + 30
    await db.conn.execute(
        "INSERT INTO hunt_marches (discord_user_id, level, hero_id, started_at, "
        "completes_at, damage, engaged_at) VALUES (?, 3, NULL, ?, ?, 1000, ?)",
        (1, started, completes, engaged_at),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _hunt_line(state)
    assert "Returning" in line
    assert "home <t:" in line


# -- daily quota ---------------------------------------------------------


async def test_quota_line_marks_claimable_at_cap(db: Database) -> None:
    await db.get_or_create_player(1)
    label = current_reset_day().isoformat()
    await db.conn.execute(
        "INSERT INTO hunt_daily_progress (discord_user_id, reset_day, kills) "
        "VALUES (?, ?, 10)",
        (1, label),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _quota_line(state)
    assert "ready to claim" in line


async def test_quota_line_marks_claimed(db: Database) -> None:
    await db.get_or_create_player(1)
    label = current_reset_day().isoformat()
    await db.conn.execute(
        "INSERT INTO hunt_daily_progress (discord_user_id, reset_day, kills, "
        "reward_claimed) VALUES (?, ?, 10, 1)",
        (1, label),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    line = _quota_line(state)
    assert "claimed" in line


# -- heroes count --------------------------------------------------------


async def test_hub_state_counts_owned_heroes(db: Database) -> None:
    await db.get_or_create_player(1)
    async with db.conn.execute(
        "SELECT id FROM heroes WHERE codename = 'ghostpink'"
    ) as cur:
        hero_id = int((await cur.fetchone())["id"])
    await db.conn.execute(
        "INSERT INTO owned_heroes (discord_user_id, hero_id, level) VALUES (?, ?, 1)",
        (1, hero_id),
    )
    await db.conn.commit()
    state = await _hub_state(db, 1)
    assert state["heroes_count"] == 1
