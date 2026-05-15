"""Tests for the Witch's Council — pure quest math + DB-backed flow."""

from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from wagame.cogs.council import (
    _contributions,
    _spawn_quest,
    active_quest,
    record_contribution,
)
from wagame.db import Database
from wagame.game.council import (
    COMPLETE_REWARD_GEMS,
    COMPLETE_REWARD_SHARDS,
    FAIL_CONSOLATION_GEMS,
    QUEST_DURATION_SECONDS,
    QUEST_KINDS,
    TARGET_BY_KIND,
    TOP_CONTRIBUTOR_BONUS,
    is_completed,
    pick_kind,
    split_rewards,
    target_for,
    threshold_amount,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    from wagame.heroes_data import sync_heroes
    database = Database(tmp_path / "council.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    yield database
    await database.close()


# -- pure logic ---------------------------------------------------------


def test_pick_kind_returns_known_kind() -> None:
    rng = random.Random(0)
    for _ in range(20):
        assert pick_kind(rng) in QUEST_KINDS


def test_target_for_covers_every_kind() -> None:
    for kind in QUEST_KINDS:
        assert target_for(kind) == TARGET_BY_KIND[kind]


def test_is_completed_truth_table() -> None:
    assert is_completed(100, 100) is True
    assert is_completed(101, 100) is True
    assert is_completed(99, 100) is False


def test_threshold_amount_is_one_percent() -> None:
    assert threshold_amount(500) == 5
    assert threshold_amount(8_000_000) == 80_000
    # Floor of 1 even at tiny targets.
    assert threshold_amount(10) == 1


def test_split_rewards_empty_returns_empty() -> None:
    assert split_rewards([], target=100, completed=True) == []


def test_split_rewards_completion_pays_top_bonus() -> None:
    # Target 500 → threshold = 5. All three qualify (5 >= 5).
    contributions = [(1, 50), (2, 30), (3, 5)]
    awards = split_rewards(contributions, target=500, completed=True)
    assert len(awards) == 3
    top = awards[0]
    assert top.user_id == 1
    assert top.gems == COMPLETE_REWARD_GEMS + TOP_CONTRIBUTOR_BONUS
    assert top.shards == COMPLETE_REWARD_SHARDS
    second = awards[1]
    assert second.gems == COMPLETE_REWARD_GEMS
    assert second.shards == COMPLETE_REWARD_SHARDS


def test_split_rewards_under_threshold_excluded() -> None:
    # Target 1000 → threshold 10; user 2 at amount 5 misses out.
    contributions = [(1, 500), (2, 5)]
    awards = split_rewards(contributions, target=1000, completed=True)
    assert [a.user_id for a in awards] == [1]


def test_split_rewards_failure_pays_consolation() -> None:
    awards = split_rewards([(1, 50), (2, 30)], target=500, completed=False)
    top = awards[0]
    assert top.gems == FAIL_CONSOLATION_GEMS + TOP_CONTRIBUTOR_BONUS // 2
    assert top.shards == 0
    second = awards[1]
    assert second.gems == FAIL_CONSOLATION_GEMS
    assert second.shards == 0


# -- DB-backed ----------------------------------------------------------


async def test_spawn_quest_inserts_active_row(db: Database) -> None:
    quest_id = await _spawn_quest(db, forced_kind="kill_tenebrals")
    row = await active_quest(db)
    assert row is not None
    assert int(row["id"]) == quest_id
    assert row["kind"] == "kill_tenebrals"
    assert int(row["target"]) == TARGET_BY_KIND["kill_tenebrals"]
    assert int(row["progress"]) == 0
    assert int(row["settled"]) == 0
    assert int(row["ends_at"]) - int(row["started_at"]) == QUEST_DURATION_SECONDS


async def test_active_quest_none_when_settled_only(db: Database) -> None:
    qid = await _spawn_quest(db, forced_kind="kill_tenebrals")
    await db.conn.execute(
        "UPDATE council_quests SET settled = 1 WHERE id = ?", (qid,)
    )
    await db.conn.commit()
    assert await active_quest(db) is None


async def test_record_contribution_only_matches_active_kind(db: Database) -> None:
    await db.get_or_create_player(1)
    await _spawn_quest(db, forced_kind="kill_tenebrals")
    await record_contribution(db, user_id=1, kind="kill_tenebrals", amount=3)
    await record_contribution(db, user_id=1, kind="gather_rss", amount=999_999)
    row = await active_quest(db)
    assert int(row["progress"]) == 3  # gather_rss didn't tick


async def test_record_contribution_aggregates_per_user(db: Database) -> None:
    await db.get_or_create_player(1)
    await _spawn_quest(db, forced_kind="kill_tenebrals")
    for _ in range(5):
        await record_contribution(db, user_id=1, kind="kill_tenebrals", amount=2)
    quest_row = await active_quest(db)
    assert int(quest_row["progress"]) == 10
    contributions = await _contributions(db, int(quest_row["id"]))
    assert contributions == [(1, 10)]


async def test_record_contribution_zero_amount_is_noop(db: Database) -> None:
    await db.get_or_create_player(1)
    await _spawn_quest(db, forced_kind="kill_tenebrals")
    await record_contribution(db, user_id=1, kind="kill_tenebrals", amount=0)
    quest_row = await active_quest(db)
    assert int(quest_row["progress"]) == 0
    contributions = await _contributions(db, int(quest_row["id"]))
    assert contributions == []


async def test_record_contribution_noop_without_active_quest(db: Database) -> None:
    await db.get_or_create_player(1)
    await record_contribution(db, user_id=1, kind="kill_tenebrals", amount=5)
    assert await active_quest(db) is None  # no quest spawned by side effect


async def test_contributions_ordered_high_to_low(db: Database) -> None:
    await db.get_or_create_player(1)
    await db.get_or_create_player(2)
    await db.get_or_create_player(3)
    await _spawn_quest(db, forced_kind="kill_tenebrals")
    await record_contribution(db, user_id=1, kind="kill_tenebrals", amount=10)
    await record_contribution(db, user_id=2, kind="kill_tenebrals", amount=30)
    await record_contribution(db, user_id=3, kind="kill_tenebrals", amount=20)
    row = await active_quest(db)
    contributions = await _contributions(db, int(row["id"]))
    assert [uid for uid, _ in contributions] == [2, 3, 1]


async def test_quest_window_uses_real_clock(db: Database) -> None:
    before = int(time.time())
    await _spawn_quest(db, forced_kind="gather_rss")
    after = int(time.time())
    row = await active_quest(db)
    assert before <= int(row["started_at"]) <= after
    assert int(row["ends_at"]) == int(row["started_at"]) + QUEST_DURATION_SECONDS
