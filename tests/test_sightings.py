"""Tests for Tenebral Sightings — pure logic + DB-backed spawn/engage."""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from wagame.cogs.sightings import (
    _active_sighting,
    _engage_sighting,
    spawn_sighting,
)
from wagame.db import Database
from wagame.game.hunt import get_tenebral
from wagame.game.sightings import (
    EXPIRY_SECONDS,
    MAX_GAP_SECONDS,
    MIN_GAP_SECONDS,
    RSS_MULTIPLIER,
    SHARD_DROP_MAX,
    SHARD_DROP_MIN,
    XP_MULTIPLIER,
    build_spec,
    is_active_player,
    is_expired,
    pick_gap,
    roll_level,
)
from wagame.heroes_data import sync_heroes
from wagame.troops_data import sync_troops


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "sightings.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    await sync_troops(database)
    yield database
    await database.close()


# -- pure logic ---------------------------------------------------------


def test_pick_gap_stays_within_window() -> None:
    rng = random.Random(0)
    for _ in range(50):
        gap = pick_gap(rng)
        assert MIN_GAP_SECONDS <= gap <= MAX_GAP_SECONDS


def test_roll_level_caps_at_twelve() -> None:
    rng = random.Random(0)
    for _ in range(50):
        assert 1 <= roll_level(12, rng) <= 12


def test_roll_level_floors_at_one() -> None:
    rng = random.Random(0)
    for _ in range(50):
        assert 1 <= roll_level(1, rng) <= 2


def test_roll_level_rejects_zero() -> None:
    with pytest.raises(ValueError):
        roll_level(0)


def test_build_spec_scales_rewards() -> None:
    rng = random.Random(0)
    base = get_tenebral(3)
    spec = build_spec(3, rng)
    assert spec.bonus_rss == base.reward_rss * RSS_MULTIPLIER
    assert spec.bonus_xp == base.reward_xp * XP_MULTIPLIER
    assert SHARD_DROP_MIN <= spec.shard_count <= SHARD_DROP_MAX


def test_is_expired_truth_table() -> None:
    assert is_expired(100, 100) is True
    assert is_expired(100, 101) is True
    assert is_expired(100, 99) is False


def test_is_active_player_window() -> None:
    now = 1_000_000
    assert is_active_player(now - 1, now) is True
    assert is_active_player(now - 30 * 24 * 3600, now) is True
    assert is_active_player(now - 31 * 24 * 3600, now) is False


# -- DB-backed spawn ---------------------------------------------------


async def test_spawn_sighting_persists_and_reschedules(db: Database) -> None:
    await db.get_or_create_player(1)
    before = int(time.time())
    sighting_id = await spawn_sighting(
        db, 1, hunt_level_unlocked=3, rng=random.Random(0)
    )
    after = int(time.time())
    assert sighting_id > 0

    row = await _active_sighting(db, 1)
    assert row is not None
    assert int(row["hp_remaining"]) == int(row["hp_max"])
    assert before + EXPIRY_SECONDS <= int(row["expires_at"]) <= after + EXPIRY_SECONDS
    assert int(row["bonus_rss"]) > 0

    async with db.conn.execute(
        "SELECT next_sighting_at FROM players WHERE discord_user_id = 1"
    ) as cur:
        next_at = int((await cur.fetchone())["next_sighting_at"])
    assert before + MIN_GAP_SECONDS <= next_at <= after + MAX_GAP_SECONDS


async def test_spawn_sighting_forced_level(db: Database) -> None:
    await db.get_or_create_player(1)
    await spawn_sighting(
        db, 1, hunt_level_unlocked=1, forced_level=7,
        rng=random.Random(0),
    )
    row = await _active_sighting(db, 1)
    assert int(row["level"]) == 7


# -- engage / claim ----------------------------------------------------


async def _grant_hero(db: Database, user_id: int, codename: str, level: int = 5) -> int:
    async with db.conn.execute(
        "SELECT id FROM heroes WHERE codename = ?", (codename,)
    ) as cur:
        hero_id = int((await cur.fetchone())["id"])
    await db.conn.execute(
        "INSERT OR IGNORE INTO owned_heroes (discord_user_id, hero_id, level) "
        "VALUES (?, ?, ?)",
        (user_id, hero_id, level),
    )
    await db.conn.execute(
        "UPDATE owned_heroes SET level = ? "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (level, user_id, hero_id),
    )
    await db.conn.commit()
    return hero_id


async def test_engage_kill_credits_rss_xp_and_shards(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink", level=1)
    sighting_id = await spawn_sighting(
        db, 1, hunt_level_unlocked=1, forced_level=1,
        rng=random.Random(0),
    )

    # Overkill damage one-shots lv1 (750 HP).
    flash, summary = await _engage_sighting(
        db, 1, sighting_id, hero_id=hero_id, damage=1_000_000
    )
    assert summary["killed"] is True
    assert "Slain" in flash.message

    # RSS credited (split across gold/food/wood).
    async with db.conn.execute(
        "SELECT gold, food, wood FROM players WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert int(row["gold"]) > 0
    assert int(row["food"]) > 0
    assert int(row["wood"]) > 0

    # Shards banked for the chosen hero.
    async with db.conn.execute(
        "SELECT SUM(count) AS n FROM hero_shards WHERE discord_user_id = 1"
    ) as cur:
        total_shards = int((await cur.fetchone())["n"] or 0)
    assert total_shards >= SHARD_DROP_MIN

    # Sighting marked claimed.
    assert await _active_sighting(db, 1) is None


async def test_engage_chip_persists_remaining_hp(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink", level=1)
    sighting_id = await spawn_sighting(
        db, 1, hunt_level_unlocked=3, forced_level=3,
        rng=random.Random(0),
    )

    flash, summary = await _engage_sighting(
        db, 1, sighting_id, hero_id=hero_id, damage=10_000
    )
    assert summary["killed"] is False
    assert "left" in flash.message

    row = await _active_sighting(db, 1)
    assert row is not None
    assert int(row["hp_remaining"]) == int(row["hp_max"]) - 10_000


async def test_engage_expired_sighting_returns_error(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink", level=1)
    # Spawn with a `now` so far in the past that the row is already expired.
    sighting_id = await spawn_sighting(
        db, 1, hunt_level_unlocked=1, forced_level=1,
        rng=random.Random(0), now=int(time.time()) - 2 * EXPIRY_SECONDS,
    )

    flash, summary = await _engage_sighting(
        db, 1, sighting_id, hero_id=hero_id, damage=1_000_000
    )
    assert summary == {"expired": True}
    assert "vanished" in flash.message

    # And the sighting is no longer active (the engage call closed it out).
    assert await _active_sighting(db, 1) is None
