"""Tests for hero summoning logic + the /summon execute path.

Split into:
- pure logic: roll_shards, roll_summon, pity behaviour, expected mean
- DB-backed: execute_summon credits/debits, owned_heroes inserts on unlock
"""

from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import pytest

from wagame.cogs.summon import _fetch_shards, execute_summon
from wagame.db import Database
from wagame.game.summon import (
    DAILY_GEM_BONUS,
    PITY_LIMIT,
    SHARDS_PER_UNLOCK,
    STARTING_GEMS,
    SUMMON_COST,
    expected_shards_per_summon,
    roll_shards,
    roll_summon,
    summon_cost,
)
from wagame.heroes_data import HeroSpec, sync_heroes


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "summon.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


# -- cost table ------------------------------------------------------------


def test_summon_cost_matches_user_tier_pinning() -> None:
    # Gold / purple / blue costs are the design pillars; lock them in.
    assert SUMMON_COST["legendary"] == 2000
    assert SUMMON_COST["epic"] == 900
    assert SUMMON_COST["rare"] == 800


def test_summon_cost_unknown_rarity_falls_back_to_rare() -> None:
    assert summon_cost("eldritch") == SUMMON_COST["rare"]


# -- shard distribution ---------------------------------------------------


def test_expected_shards_per_summon_in_target_range() -> None:
    # Between steep (~18) and middle (~27), we tuned to ~20.
    mean = expected_shards_per_summon()
    assert 18.0 <= mean <= 22.0


def test_roll_shards_floor_is_one() -> None:
    rng = random.Random(0)
    for _ in range(500):
        count, _ = roll_shards(rng)
        assert count >= 1


def test_roll_shards_jackpot_is_rare(rng_iterations: int = 5000) -> None:
    rng = random.Random(42)
    jackpots = 0
    for _ in range(rng_iterations):
        _, jackpot = roll_shards(rng)
        if jackpot:
            jackpots += 1
    rate = jackpots / rng_iterations
    # Band weight is 1%; allow ±0.6pp slack for 5k samples.
    assert 0.004 <= rate <= 0.016


def test_distribution_band_share_is_within_tolerance() -> None:
    """Sanity-check that the bands fire roughly at their declared weights."""
    rng = random.Random(123)
    bucket = Counter()
    for _ in range(10_000):
        count, _ = roll_shards(rng)
        if count <= 9:
            bucket["a"] += 1
        elif count <= 29:
            bucket["b"] += 1
        elif count <= 79:
            bucket["c"] += 1
        elif count <= 99:
            bucket["d"] += 1
        else:
            bucket["e"] += 1
    assert 0.50 <= bucket["a"] / 10_000 <= 0.60
    assert 0.23 <= bucket["b"] / 10_000 <= 0.33
    assert 0.10 <= bucket["c"] / 10_000 <= 0.16


# -- pity ------------------------------------------------------------------


def test_pity_caps_count_at_limit() -> None:
    """No combination of unlucky rolls can exceed PITY_LIMIT without unlock."""
    rng = random.Random(2026)
    # Player who's been spectacularly unlucky: 99 summons, only 1 shard.
    result = roll_summon(current_shards=1, pity=PITY_LIMIT - 1, rng=rng)
    assert result.unlocked_now
    assert result.pity_activated
    assert result.new_total >= SHARDS_PER_UNLOCK
    assert result.new_pity == 0


def test_pity_does_not_clobber_a_naturally_big_roll() -> None:
    """When the natural roll already unlocks, pity stays out of the way."""
    # Force the natural roll to land in the jackpot band: we just rerun until
    # we see a jackpot, then check the pity flag is False.
    for seed in range(1000):
        r = random.Random(seed)
        result = roll_summon(current_shards=0, pity=PITY_LIMIT - 1, rng=r)
        if result.jackpot:
            assert result.unlocked_now
            assert not result.pity_activated
            return
    pytest.fail("no jackpot in 1000 seeds — RNG seam broken")


def test_pity_counter_resets_on_unlock() -> None:
    rng = random.Random(7)
    # Pre-stack to 99 shards so any summon >= 1 unlocks.
    result = roll_summon(current_shards=99, pity=50, rng=rng)
    assert result.unlocked_now
    assert result.new_pity == 0


def test_pity_counter_increments_when_no_unlock() -> None:
    rng = random.Random(99)
    result = roll_summon(current_shards=0, pity=10, rng=rng)
    if not result.unlocked_now:
        assert result.new_pity == 11


def test_negative_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        roll_summon(current_shards=-1, pity=0)
    with pytest.raises(ValueError):
        roll_summon(current_shards=0, pity=-1)


# -- DB-backed execute_summon ------------------------------------------------


async def _seed_hero(db: Database, *, rarity: str = "rare") -> int:
    spec = HeroSpec(
        codename="testhero",
        name="Test Hero",
        rarity=rarity,
        element=None,
        house=None,
        terrain=None,
        release_date=None,
        image_url=None,
        bonuses=(),
        tags=(),
    )
    await sync_heroes(db, [spec])
    async with db.conn.execute("SELECT id FROM heroes WHERE codename = 'testhero'") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row["id"])


async def test_new_player_starts_with_seeded_gems(db: Database) -> None:
    # get_or_create_player also runs the daily auto-claim on the very first
    # call, so a fresh account ends up at STARTING_GEMS plus today's bonus.
    # See tests/test_daily.py for the standalone proof of that flow.
    player = await db.get_or_create_player(1)
    assert player["gems"] == STARTING_GEMS + DAILY_GEM_BONUS


async def test_execute_summon_debits_gems_and_credits_shards(db: Database) -> None:
    hero_id = await _seed_hero(db, rarity="rare")
    await db.get_or_create_player(1)

    async with db.conn.execute("SELECT * FROM heroes WHERE id = ?", (hero_id,)) as cur:
        hero_row = await cur.fetchone()
    assert hero_row is not None

    async with db.conn.execute(
        "SELECT gems FROM players WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    starting_balance = int(row["gems"])

    result, _ = await execute_summon(db, 1, hero_row)
    async with db.conn.execute(
        "SELECT gems FROM players WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert int(row["gems"]) == starting_balance - SUMMON_COST["rare"]

    count, _pity = await _fetch_shards(db, 1, hero_id)
    assert count == result.shards_rolled


async def test_execute_summon_unlocks_hero_on_threshold(db: Database) -> None:
    hero_id = await _seed_hero(db, rarity="rare")
    await db.get_or_create_player(1)
    # Pre-stack to 99 shards so any roll triggers an unlock.
    await db.conn.execute(
        "INSERT INTO hero_shards (discord_user_id, hero_id, count, pity_pulls) "
        "VALUES (1, ?, 99, 50)",
        (hero_id,),
    )
    await db.conn.commit()

    async with db.conn.execute("SELECT * FROM heroes WHERE id = ?", (hero_id,)) as cur:
        hero_row = await cur.fetchone()
    assert hero_row is not None
    result, flash = await execute_summon(db, 1, hero_row)
    assert result.unlocked_now
    # Pity counter cleared.
    _, pity = await _fetch_shards(db, 1, hero_id)
    assert pity == 0
    # owned_heroes now carries the hero.
    async with db.conn.execute(
        "SELECT level FROM owned_heroes WHERE discord_user_id = 1 AND hero_id = ?",
        (hero_id,),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["level"] == 1
    assert flash.message.lower().startswith("🎉")


async def test_summon_persists_pity_counter(db: Database) -> None:
    hero_id = await _seed_hero(db, rarity="rare")
    await db.get_or_create_player(1)
    async with db.conn.execute("SELECT * FROM heroes WHERE id = ?", (hero_id,)) as cur:
        hero_row = await cur.fetchone()
    assert hero_row is not None

    # Burn summons until either unlock or counter advances. We just verify the
    # counter is monotonic and matches what's in the DB.
    for expected in range(1, 4):
        result, _ = await execute_summon(db, 1, hero_row)
        if result.unlocked_now:
            return
        _, pity = await _fetch_shards(db, 1, hero_id)
        assert pity == expected
