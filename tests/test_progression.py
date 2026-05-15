"""Tests for player XP + hunt-kill shard drops."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from wagame.db import Database
from wagame.game.player_xp import (
    PLAYER_LEVEL_CAP,
    apply_player_xp,
    title_for,
    xp_to_next,
)
from wagame.game.progression import (
    drop_kill_shards,
    grant_player_xp,
    kill_shard_drop_count,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    from wagame.heroes_data import sync_heroes
    database = Database(tmp_path / "progression.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    yield database
    await database.close()


# -- player_xp curve ---------------------------------------------------


def test_xp_to_next_matches_hero_curve() -> None:
    assert xp_to_next(1) == 100


def test_xp_to_next_rejects_zero() -> None:
    with pytest.raises(ValueError):
        xp_to_next(0)


def test_apply_player_xp_basic_levelup() -> None:
    result = apply_player_xp(level=1, xp=0, gained=100)
    assert result.new_level == 2
    assert result.new_xp == 0
    assert result.levels_gained == 1


def test_apply_player_xp_partial() -> None:
    result = apply_player_xp(level=1, xp=0, gained=50)
    assert result.new_level == 1
    assert result.new_xp == 50
    assert result.levels_gained == 0


def test_apply_player_xp_caps_with_banked_excess() -> None:
    result = apply_player_xp(
        level=PLAYER_LEVEL_CAP, xp=0, gained=10_000_000
    )
    assert result.new_level == PLAYER_LEVEL_CAP
    assert result.new_xp == 10_000_000
    assert result.levels_gained == 0


def test_title_bands() -> None:
    assert title_for(1) == "Novice"
    assert title_for(5) == "Apprentice"
    assert title_for(10) == "Adept"
    assert title_for(25) == "Mage"
    assert title_for(50) == "Archmage"


# -- kill shard drop math ---------------------------------------------


def test_kill_shard_drop_count_returns_zero_or_positive() -> None:
    rng = random.Random(0)
    for level in range(1, 13):
        for _ in range(20):
            count = kill_shard_drop_count(level, rng)
            assert count >= 0


def test_kill_shard_drop_chance_increases_with_level() -> None:
    """Across many seeds, lv12 should drop more often than lv1."""
    lv1_hits = sum(
        1 for s in range(500)
        if kill_shard_drop_count(1, random.Random(s)) > 0
    )
    lv12_hits = sum(
        1 for s in range(500)
        if kill_shard_drop_count(12, random.Random(s)) > 0
    )
    assert lv12_hits > lv1_hits


def test_kill_shard_drop_zero_level_is_noop() -> None:
    assert kill_shard_drop_count(0, random.Random(0)) == 0


# -- DB-backed grant ---------------------------------------------------


async def test_grant_player_xp_zero_amount_returns_current_level(
    db: Database,
) -> None:
    await db.get_or_create_player(1)
    level, gained, shards = await grant_player_xp(db, 1, 0)
    assert level == 1
    assert gained == 0
    assert shards == 0


async def test_grant_player_xp_credits_xp(db: Database) -> None:
    await db.get_or_create_player(1)
    level, gained, _ = await grant_player_xp(db, 1, 50)
    assert level == 1
    assert gained == 0
    async with db.conn.execute(
        "SELECT player_xp FROM players WHERE discord_user_id = 1"
    ) as cur:
        assert int((await cur.fetchone())["player_xp"]) == 50


async def test_grant_player_xp_level_up_pays_gem_milestone(
    db: Database,
) -> None:
    from wagame.game.player_xp import PLAYER_LEVELUP_GEMS
    await db.get_or_create_player(1)
    async with db.conn.execute(
        "SELECT gems FROM players WHERE discord_user_id = 1"
    ) as cur:
        before = int((await cur.fetchone())["gems"])
    level, gained, _shards = await grant_player_xp(db, 1, 100)  # exactly 1 level
    assert level == 2
    assert gained == 1
    async with db.conn.execute(
        "SELECT gems FROM players WHERE discord_user_id = 1"
    ) as cur:
        after = int((await cur.fetchone())["gems"])
    assert after == before + PLAYER_LEVELUP_GEMS


async def test_drop_kill_shards_credits_when_rng_succeeds(db: Database) -> None:
    """Force a high-chance level and a seed that lands the hit."""
    await db.get_or_create_player(1)
    # lv12 hits ~68% of rolls; with seed 0, kill_shard_drop_count(12, ...) > 0.
    for seed in range(20):
        rng = random.Random(seed)
        count = kill_shard_drop_count(12, rng)
        if count > 0:
            # Re-roll with a fresh seed used for both the chance check and
            # the hero pick, mirroring drop_kill_shards' shared rng.
            credited, hero_id = await drop_kill_shards(
                db, 1, 12, rng=random.Random(seed),
            )
            assert credited == count
            assert hero_id is not None
            async with db.conn.execute(
                "SELECT SUM(count) AS n FROM hero_shards "
                "WHERE discord_user_id = 1"
            ) as cur:
                assert int((await cur.fetchone())["n"]) == credited
            return
    pytest.fail("never landed a kill shard in 20 seeds at lv12")
