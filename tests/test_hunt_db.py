"""DB-backed tests for the /hunt cog helpers — kill cascade, daily quota,
energy regen persistence."""

from __future__ import annotations

from pathlib import Path

import pytest

from wagame.cogs.hunt import (
    _bump_daily,
    _claim_daily,
    _daily_row,
    _engage_march,
    _fetch_owned_heroes,
    _fetch_troops,
    _finalize_march,
    _regen_and_persist,
    _resolve_march,
    _spawn_or_get,
    _spend_energy,
)
from wagame.db import Database
from wagame.game.daily import current_reset_day
from wagame.game.hunt import (
    DAILY_QUOTA_KILLS,
    DAILY_QUOTA_REWARD_GEMS,
    ENERGY_CAP,
    get_tenebral,
)
from wagame.heroes_data import sync_heroes
from wagame.troops_data import sync_troops


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "hunt.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    await sync_troops(database)
    yield database
    await database.close()


# -- energy ---------------------------------------------------------------


async def test_new_player_starts_at_full_energy(db: Database) -> None:
    await db.get_or_create_player(1)
    energy, _ = await _regen_and_persist(db, 1)
    assert energy == ENERGY_CAP


async def test_spend_energy_deducts_and_blocks_when_empty(db: Database) -> None:
    await db.get_or_create_player(1)
    assert await _spend_energy(db, 1, 100)
    energy, _ = await _regen_and_persist(db, 1)
    assert energy == ENERGY_CAP - 100

    # Drain to zero and verify next spend fails.
    assert await _spend_energy(db, 1, energy)
    assert not await _spend_energy(db, 1, 1)


# -- spawning -------------------------------------------------------------


async def test_spawn_or_get_creates_fresh_then_reuses(db: Database) -> None:
    await db.get_or_create_player(1)
    spawn = await _spawn_or_get(db, 1, 3)
    spec = get_tenebral(3)
    assert int(spawn["hp_remaining"]) == spec.hp
    assert int(spawn["hp_max"]) == spec.hp

    # Manually wound the mob and re-fetch; we get the same row back.
    await db.conn.execute(
        "UPDATE tenebral_spawns SET hp_remaining = ? "
        "WHERE discord_user_id = ? AND level = ?",
        (10, 1, 3),
    )
    await db.conn.commit()
    again = await _spawn_or_get(db, 1, 3)
    assert int(again["hp_remaining"]) == 10


# -- kill cascade ---------------------------------------------------------


async def _grant_hero(db: Database, user_id: int, codename: str, level: int = 5) -> int:
    async with db.conn.execute(
        "SELECT id FROM heroes WHERE codename = ?", (codename,)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    hero_id = int(row["id"])
    await db.conn.execute(
        "INSERT INTO owned_heroes (discord_user_id, hero_id, level, xp) "
        "VALUES (?, ?, ?, 0)",
        (user_id, hero_id, level),
    )
    await db.conn.commit()
    return hero_id


async def _grant_troops(db: Database, user_id: int, codename: str, count: int) -> None:
    await db.conn.execute(
        "INSERT INTO owned_troops (discord_user_id, troop_codename, count) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(discord_user_id, troop_codename) DO UPDATE SET count = count + ?",
        (user_id, codename, count, count),
    )
    await db.conn.commit()


async def _insert_march(db: Database, user_id: int, level: int, hero_id: int, damage: int) -> int:
    async with db.conn.execute(
        """
        INSERT INTO hunt_marches
          (discord_user_id, level, hero_id, started_at, completes_at, damage)
        VALUES (?, ?, ?, 0, 0, ?)
        """,
        (user_id, level, hero_id, damage),
    ) as cur:
        return int(cur.lastrowid)


async def test_resolve_march_chips_hp_when_not_killed(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink")
    march_id = await _insert_march(db, 1, level=3, hero_id=hero_id, damage=100_000)
    flash, summary = await _resolve_march(db, march_id)
    assert summary["killed"] is False
    spec = get_tenebral(3)
    assert summary["hp_after"] == spec.hp - 100_000
    assert "Lv3" in flash.message


async def test_engage_applies_damage_without_resolving(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink")
    march_id = await _insert_march(db, 1, level=3, hero_id=hero_id, damage=100_000)
    flash, summary = await _engage_march(db, march_id)
    assert summary["killed"] is False
    assert "Lv3" in flash.message

    async with db.conn.execute(
        "SELECT engaged_at, resolved FROM hunt_marches WHERE id = ?", (march_id,)
    ) as cur:
        row = await cur.fetchone()
    assert row["engaged_at"] is not None  # engagement timestamp recorded
    assert int(row["resolved"]) == 0      # but return leg still pending


async def test_engage_is_idempotent(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink")
    march_id = await _insert_march(db, 1, level=3, hero_id=hero_id, damage=100_000)
    await _engage_march(db, march_id)
    # Mob HP after first engagement.
    async with db.conn.execute(
        "SELECT hp_remaining FROM tenebral_spawns WHERE discord_user_id = 1 AND level = 3"
    ) as cur:
        hp_after_first = int((await cur.fetchone())["hp_remaining"])

    flash, summary = await _engage_march(db, march_id)
    assert summary == {}
    assert "Already engaged" in flash.message
    # Mob HP unchanged.
    async with db.conn.execute(
        "SELECT hp_remaining FROM tenebral_spawns WHERE discord_user_id = 1 AND level = 3"
    ) as cur:
        assert int((await cur.fetchone())["hp_remaining"]) == hp_after_first


async def test_finalize_marks_resolved(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink")
    march_id = await _insert_march(db, 1, level=3, hero_id=hero_id, damage=100_000)
    await _engage_march(db, march_id)
    await _finalize_march(db, march_id)
    async with db.conn.execute(
        "SELECT resolved FROM hunt_marches WHERE id = ?", (march_id,)
    ) as cur:
        assert int((await cur.fetchone())["resolved"]) == 1


async def test_resolve_march_kills_and_awards_reward(db: Database) -> None:
    await db.get_or_create_player(1)
    hero_id = await _grant_hero(db, 1, "ghostpink", level=1)
    spec = get_tenebral(1)
    march_id = await _insert_march(db, 1, level=1, hero_id=hero_id, damage=spec.hp * 10)
    flash, summary = await _resolve_march(db, march_id)
    assert summary["killed"] is True

    # Mob despawned.
    async with db.conn.execute(
        "SELECT * FROM tenebral_spawns WHERE discord_user_id = 1 AND level = 1"
    ) as cur:
        assert await cur.fetchone() is None

    # RSS credited.
    async with db.conn.execute(
        "SELECT gold, food, wood FROM players WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert int(row["gold"]) > 0
    assert int(row["food"]) > 0
    assert int(row["wood"]) > 0

    # Hero XP applied.
    async with db.conn.execute(
        "SELECT level, xp FROM owned_heroes WHERE discord_user_id = 1 AND hero_id = ?",
        (hero_id,),
    ) as cur:
        hero_row = await cur.fetchone()
    assert int(hero_row["level"]) >= 2  # 100 xp reward at lv1 -> cascades.

    # Daily quota bumped.
    daily = await _daily_row(db, 1)
    assert int(daily["kills"]) == 1

    assert "Slain" in flash.message


# -- daily quota ----------------------------------------------------------


async def test_daily_row_keyed_to_reset_day(db: Database) -> None:
    await db.get_or_create_player(1)
    row = await _daily_row(db, 1)
    assert row["reset_day"] == current_reset_day().isoformat()
    assert int(row["kills"]) == 0


async def test_bump_daily_accumulates(db: Database) -> None:
    await db.get_or_create_player(1)
    for expected in range(1, 4):
        kills = await _bump_daily(db, 1)
        assert kills == expected


async def test_claim_daily_requires_quota(db: Database) -> None:
    await db.get_or_create_player(1)
    ok, flash = await _claim_daily(db, 1)
    assert ok is False
    assert "more kill" in flash.message


async def test_claim_daily_pays_out_once(db: Database) -> None:
    await db.get_or_create_player(1)
    for _ in range(DAILY_QUOTA_KILLS):
        await _bump_daily(db, 1)
    async with db.conn.execute("SELECT gems FROM players WHERE discord_user_id = 1") as cur:
        gems_before = int((await cur.fetchone())["gems"])

    ok, _ = await _claim_daily(db, 1)
    assert ok is True
    async with db.conn.execute("SELECT gems FROM players WHERE discord_user_id = 1") as cur:
        # Daily pays DAILY_QUOTA_REWARD_GEMS; player XP from the claim can
        # also fire level-up gem bonuses on top.
        assert int((await cur.fetchone())["gems"]) >= gems_before + DAILY_QUOTA_REWARD_GEMS

    # Second claim is a no-op.
    ok, flash = await _claim_daily(db, 1)
    assert ok is False
    assert "already claimed" in flash.message


# -- inventory helpers ---------------------------------------------------


async def test_fetch_troops_skips_empty_stacks(db: Database) -> None:
    await db.get_or_create_player(1)
    await _grant_troops(db, 1, "catsith", 100)
    await _grant_troops(db, 1, "gryphon", 0)
    stacks = await _fetch_troops(db, 1)
    codenames = {s.codename for s in stacks}
    assert "catsith" in codenames
    assert "gryphon" not in codenames


async def test_fetch_owned_heroes_sorted_by_rarity(db: Database) -> None:
    await db.get_or_create_player(1)
    # ghostpink is epic, give one. Need a second hero of different rarity
    # to verify ordering — grab any common from the catalog if available.
    await _grant_hero(db, 1, "ghostpink", level=5)
    async with db.conn.execute(
        "SELECT codename FROM heroes WHERE rarity = 'common' LIMIT 1"
    ) as cur:
        common_row = await cur.fetchone()
    if common_row:
        await _grant_hero(db, 1, common_row["codename"], level=2)
        owned = await _fetch_owned_heroes(db, 1)
        # ghostpink (epic) must come before the common.
        assert owned[0]["name"] == "Roselda Graves"
