"""Smoke tests for Hero Bonds — pure math + DB round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from wagame.cogs.bonds import bond_bonus_pct_for, record_bond
from wagame.db import Database
from wagame.game.bonds import canonical_pair, tier_for


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    from wagame.heroes_data import sync_heroes
    database = Database(tmp_path / "bonds.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    yield database
    await database.close()


def test_canonical_pair_orders_low_first() -> None:
    assert canonical_pair(5, 3) == (3, 5)
    assert canonical_pair(3, 5) == (3, 5)
    with pytest.raises(ValueError):
        canonical_pair(3, 3)


def test_tier_thresholds_step_up() -> None:
    assert tier_for(0).level == 0
    assert tier_for(0).bonus_pct == 0
    assert tier_for(10).level == 1
    assert tier_for(50).level == 2
    assert tier_for(150).level == 3
    assert tier_for(500).level == 4
    # Top tier sticks.
    assert tier_for(10_000).level == 4


async def test_record_bond_accumulates_and_buff_unlocks(db: Database) -> None:
    await db.get_or_create_player(1)
    async with db.conn.execute(
        "SELECT id FROM heroes ORDER BY id LIMIT 2"
    ) as cur:
        rows = await cur.fetchall()
    a_id, b_id = int(rows[0]["id"]), int(rows[1]["id"])

    # 4 kills @ ≥3 points each (≥12 by base, more with affinity) crosses Lv1.
    for _ in range(4):
        awarded = await record_bond(db, 1, a_id, b_id, killed=True)
        assert awarded >= 3

    bonus = await bond_bonus_pct_for(db, 1, a_id, b_id)
    assert bonus >= 5  # Lv1 grants +5%


async def test_record_bond_noop_without_support(db: Database) -> None:
    await db.get_or_create_player(1)
    assert await record_bond(db, 1, 1, None, killed=True) == 0
    assert await record_bond(db, 1, 1, 1, killed=True) == 0
