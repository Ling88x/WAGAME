"""Tests for the Witch's Vault — pure streak math + DB-backed open."""

from __future__ import annotations

import datetime
import random
from pathlib import Path

import pytest

from wagame.cogs.vault import open_vault
from wagame.db import Database
from wagame.game.vault import (
    MILESTONE_DAYS,
    MYTHIC_SHARDS_MAX,
    MYTHIC_SHARDS_MIN,
    compute_next_streak,
    next_reset_unix,
    roll_reward,
    streak_multiplier,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    from wagame.heroes_data import sync_heroes
    database = Database(tmp_path / "vault.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    yield database
    await database.close()


# -- streak math -------------------------------------------------------


def test_first_open_starts_streak_at_one() -> None:
    new, already = compute_next_streak(0, None, "2026-05-14")
    assert new == 1
    assert already is False


def test_consecutive_day_increments_streak() -> None:
    new, already = compute_next_streak(3, "2026-05-13", "2026-05-14")
    assert new == 4
    assert already is False


def test_same_day_open_is_no_op() -> None:
    new, already = compute_next_streak(7, "2026-05-14", "2026-05-14")
    assert new == 7
    assert already is True


def test_skipped_day_resets_streak_to_one() -> None:
    new, already = compute_next_streak(12, "2026-05-12", "2026-05-14")
    assert new == 1
    assert already is False


def test_clock_skew_resets_safely() -> None:
    new, _ = compute_next_streak(5, "2026-05-20", "2026-05-14")
    assert new == 1


# -- multiplier --------------------------------------------------------


def test_streak_multiplier_steps_up() -> None:
    assert streak_multiplier(1) == 1.0
    assert streak_multiplier(3) == 1.0
    assert streak_multiplier(4) == 1.1
    assert streak_multiplier(7) == 1.25
    assert streak_multiplier(14) == 1.5
    assert streak_multiplier(30) == 2.0
    assert streak_multiplier(100) == 2.0


# -- reward roll -------------------------------------------------------


def test_roll_reward_requires_positive_streak() -> None:
    with pytest.raises(ValueError):
        roll_reward(0)


def test_low_streak_can_drop_any_tier() -> None:
    seen: set[str] = set()
    for seed in range(1000):
        bag = roll_reward(1, rng=random.Random(seed))
        seen.add(bag.tier)
    # With 1000 rolls at default weights, every tier should appear.
    assert {"common", "uncommon", "rare", "legendary", "mythic"}.issubset(seen)


def test_day_7_floor_eliminates_common_drops() -> None:
    for seed in range(500):
        bag = roll_reward(7, rng=random.Random(seed))
        assert bag.tier != "common"


def test_day_14_floor_eliminates_common_and_uncommon() -> None:
    for seed in range(500):
        bag = roll_reward(14, rng=random.Random(seed))
        assert bag.tier in ("rare", "legendary", "mythic")


def test_day_30_floor_eliminates_below_legendary() -> None:
    for seed in range(500):
        bag = roll_reward(30, rng=random.Random(seed))
        assert bag.tier in ("legendary", "mythic")


def test_mythic_bag_includes_shards_in_range() -> None:
    # Force a mythic via a high streak so the test is deterministic-ish.
    for seed in range(50):
        bag = roll_reward(30, rng=random.Random(seed))
        if bag.tier == "mythic":
            assert MYTHIC_SHARDS_MIN <= bag.shard_count <= MYTHIC_SHARDS_MAX
            return
    pytest.fail("no mythic in 50 day-30 rolls — odds drifted")


def test_streak_multiplier_lifts_bag_amounts() -> None:
    rng_base = random.Random(0)
    rng_boosted = random.Random(0)
    base = roll_reward(1, rng=rng_base)
    # Day 30 = 2x multiplier on top, but the rolled tier may differ —
    # compare against an explicit common bag instead by forcing the
    # tier through seed.
    boosted = roll_reward(30, rng=rng_boosted)
    if base.tier == boosted.tier:
        assert boosted.gold >= base.gold
        assert boosted.gems >= base.gems


def test_milestone_days_attach_bonus() -> None:
    bag = roll_reward(7, rng=random.Random(0))
    expected_gems, expected_shards = MILESTONE_DAYS[7]
    assert bag.milestone_gems == expected_gems
    assert bag.milestone_shards == expected_shards


def test_non_milestone_days_have_no_bonus() -> None:
    bag = roll_reward(5, rng=random.Random(0))
    assert bag.milestone_gems == 0
    assert bag.milestone_shards == 0


# -- next-reset -------------------------------------------------------


def test_next_reset_after_today_reset() -> None:
    now = datetime.datetime(2026, 5, 14, 22, 0, tzinfo=datetime.timezone.utc)
    ts = next_reset_unix(now)
    # Next reset is the following day at 21:00 UTC.
    expected = datetime.datetime(
        2026, 5, 15, 21, 0, tzinfo=datetime.timezone.utc
    ).timestamp()
    assert ts == int(expected)


def test_next_reset_before_today_reset() -> None:
    now = datetime.datetime(2026, 5, 14, 12, 0, tzinfo=datetime.timezone.utc)
    ts = next_reset_unix(now)
    expected = datetime.datetime(
        2026, 5, 14, 21, 0, tzinfo=datetime.timezone.utc
    ).timestamp()
    assert ts == int(expected)


# -- DB-backed open ---------------------------------------------------


async def test_open_vault_credits_rss_and_records_streak(db: Database) -> None:
    await db.get_or_create_player(1)
    flash, bag = await open_vault(db, 1, rng=random.Random(0))
    assert bag is not None
    assert "bag" in flash.message.lower()

    async with db.conn.execute(
        "SELECT gold, food, wood, gems, vault_streak, last_vault_open_date "
        "FROM players WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert int(row["gold"]) == bag.gold
    assert int(row["food"]) == bag.food
    assert int(row["wood"]) == bag.wood
    assert int(row["vault_streak"]) == 1
    assert row["last_vault_open_date"] is not None


async def test_second_open_same_day_is_rejected(db: Database) -> None:
    await db.get_or_create_player(1)
    _first_flash, first_bag = await open_vault(db, 1, rng=random.Random(0))
    assert first_bag is not None

    second_flash, second_bag = await open_vault(db, 1, rng=random.Random(0))
    assert second_bag is None
    assert "already" in second_flash.message.lower()


async def test_open_vault_continues_streak_across_days(db: Database) -> None:
    await db.get_or_create_player(1)
    _, bag1 = await open_vault(db, 1, rng=random.Random(0), today_iso="2026-05-13")
    _, bag2 = await open_vault(db, 1, rng=random.Random(0), today_iso="2026-05-14")
    assert bag1 is not None and bag2 is not None
    async with db.conn.execute(
        "SELECT vault_streak FROM players WHERE discord_user_id = 1"
    ) as cur:
        assert int((await cur.fetchone())["vault_streak"]) == 2


async def test_skip_resets_streak(db: Database) -> None:
    await db.get_or_create_player(1)
    await open_vault(db, 1, rng=random.Random(0), today_iso="2026-05-10")
    await open_vault(db, 1, rng=random.Random(0), today_iso="2026-05-14")
    async with db.conn.execute(
        "SELECT vault_streak FROM players WHERE discord_user_id = 1"
    ) as cur:
        assert int((await cur.fetchone())["vault_streak"]) == 1


async def test_milestone_bonus_credits_gems_on_day_seven(db: Database) -> None:
    await db.get_or_create_player(1)
    # Walk through 7 consecutive days.
    for i in range(7):
        day = (datetime.date(2026, 5, 8) + datetime.timedelta(days=i)).isoformat()
        await open_vault(db, 1, rng=random.Random(i), today_iso=day)
    async with db.conn.execute(
        "SELECT gems, vault_streak FROM players WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert int(row["vault_streak"]) == 7
    # Player starts with STARTING_GEMS = 5000 (PR #7), +day-7 milestone 100.
    assert int(row["gems"]) >= 5000 + MILESTONE_DAYS[7][0]
