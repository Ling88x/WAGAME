"""Tests for Ghost Arena — pure ELO math + DB-backed match runner."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from wagame.cogs.arena import _eligible_user_ids, _pick_pair, _record_outcome
from wagame.db import Database
from wagame.game.arena import (
    ARENA_RATING_FLOOR,
    ARENA_STARTING_RATING,
    GEMS_DRAW,
    GEMS_LOSS,
    GEMS_WIN,
    apply_elo,
    expected_score,
    reward_gems,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    from wagame.heroes_data import sync_heroes
    database = Database(tmp_path / "arena.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    yield database
    await database.close()


# -- pure ELO ----------------------------------------------------------


def test_expected_score_symmetric_at_equal_ratings() -> None:
    assert expected_score(1500, 1500) == pytest.approx(0.5)


def test_expected_score_favors_higher_rating() -> None:
    higher = expected_score(1600, 1400)
    lower = expected_score(1400, 1600)
    assert higher > 0.7
    assert lower < 0.3
    assert higher + lower == pytest.approx(1.0)


def test_apply_elo_zero_sum() -> None:
    change = apply_elo(1500, 1500, 1.0)
    # Equal ratings + A wins → A gains exactly half of K (16), B loses 16.
    assert change.delta_a == 16
    assert change.delta_b == -16
    assert change.new_a == 1516
    assert change.new_b == 1484


def test_apply_elo_underdog_win_gains_more() -> None:
    underdog = apply_elo(1300, 1700, 1.0)
    favored = apply_elo(1700, 1300, 1.0)
    assert underdog.delta_a > favored.delta_a


def test_apply_elo_draw_is_small_correction() -> None:
    change = apply_elo(1300, 1700, 0.5)
    # Lower-rated player gains a bit on draw because they over-performed.
    assert change.delta_a > 0
    assert change.delta_b < 0


def test_apply_elo_rejects_bad_score() -> None:
    with pytest.raises(ValueError):
        apply_elo(1000, 1000, 0.42)


def test_apply_elo_floors_rating() -> None:
    change = apply_elo(ARENA_RATING_FLOOR, 2500, 0.0)
    assert change.new_a == ARENA_RATING_FLOOR  # already at floor, can't drop


# -- rewards ----------------------------------------------------------


def test_reward_gems_win_loss() -> None:
    a, b = reward_gems(1.0)
    assert a == GEMS_WIN
    assert b == GEMS_LOSS


def test_reward_gems_draw_split() -> None:
    a, b = reward_gems(0.5)
    assert a == GEMS_DRAW
    assert b == GEMS_DRAW


def test_reward_gems_loss_for_a() -> None:
    a, b = reward_gems(0.0)
    assert a == GEMS_LOSS
    assert b == GEMS_WIN


# -- DB-backed --------------------------------------------------------


async def _enroll(db: Database, user_id: int) -> None:
    await db.get_or_create_player(user_id)
    await db.conn.execute(
        "UPDATE players SET arena_enrolled = 1 WHERE discord_user_id = ?",
        (user_id,),
    )
    await db.conn.commit()


async def test_eligible_pool_requires_enrollment(db: Database) -> None:
    await db.get_or_create_player(1)  # not enrolled
    await _enroll(db, 2)
    pool = await _eligible_user_ids(db)
    assert pool == [2]


async def test_pick_pair_returns_none_with_one_candidate(db: Database) -> None:
    await _enroll(db, 1)
    assert await _pick_pair(db, rng=random.Random(0)) is None


async def test_pick_pair_with_two_enrolled(db: Database) -> None:
    await _enroll(db, 1)
    await _enroll(db, 2)
    pair = await _pick_pair(db, rng=random.Random(0))
    assert pair is not None
    assert set(pair) == {1, 2}


async def _gems(db: Database, user_id: int) -> int:
    async with db.conn.execute(
        "SELECT gems FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return int((await cur.fetchone())["gems"])


async def test_record_outcome_win_updates_ratings_and_counts(db: Database) -> None:
    await _enroll(db, 1)
    await _enroll(db, 2)
    gems_a_before = await _gems(db, 1)
    gems_b_before = await _gems(db, 2)

    new_a, new_b, delta_a, delta_b = await _record_outcome(
        db, a_id=1, b_id=2, score_a=1.0,
    )
    assert new_a == ARENA_STARTING_RATING + delta_a
    assert new_b == ARENA_STARTING_RATING + delta_b
    assert delta_a > 0
    assert delta_b < 0

    async with db.conn.execute(
        "SELECT arena_wins, arena_losses, arena_draws FROM players "
        "WHERE discord_user_id = 1"
    ) as cur:
        a_row = await cur.fetchone()
    assert int(a_row["arena_wins"]) == 1
    assert int(a_row["arena_losses"]) == 0
    assert int(a_row["arena_draws"]) == 0
    # Win pays GEMS_WIN; player XP from the match can additionally fire a
    # level-up milestone bonus, so assert at least the arena reward.
    assert await _gems(db, 1) >= gems_a_before + GEMS_WIN

    async with db.conn.execute(
        "SELECT arena_wins, arena_losses, arena_draws FROM players "
        "WHERE discord_user_id = 2"
    ) as cur:
        b_row = await cur.fetchone()
    assert int(b_row["arena_wins"]) == 0
    assert int(b_row["arena_losses"]) == 1
    assert int(b_row["arena_draws"]) == 0
    assert await _gems(db, 2) >= gems_b_before + GEMS_LOSS


async def test_record_outcome_draw_splits_no_ws_no_ls(db: Database) -> None:
    await _enroll(db, 1)
    await _enroll(db, 2)
    before_1 = await _gems(db, 1)
    before_2 = await _gems(db, 2)
    await _record_outcome(db, a_id=1, b_id=2, score_a=0.5)
    async with db.conn.execute(
        "SELECT arena_wins, arena_losses, arena_draws FROM players "
        "WHERE discord_user_id IN (1, 2) ORDER BY discord_user_id"
    ) as cur:
        rows = await cur.fetchall()
    for r in rows:
        assert int(r["arena_wins"]) == 0
        assert int(r["arena_losses"]) == 0
        assert int(r["arena_draws"]) == 1
    assert await _gems(db, 1) >= before_1 + GEMS_DRAW
    assert await _gems(db, 2) >= before_2 + GEMS_DRAW
