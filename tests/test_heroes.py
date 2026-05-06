"""Tests for the heroes loader and owned_heroes invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wagame.db import Database
from wagame.heroes_data import HEROES_JSON_PATH, HeroSpec, load_specs, sync_heroes


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


def test_seed_json_loads() -> None:
    specs = load_specs()
    assert len(specs) >= 1
    codenames = [s.codename for s in specs]
    assert len(codenames) == len(set(codenames)), "duplicate codenames in heroes.json"


async def test_sync_heroes_populates_table(db: Database) -> None:
    count = await sync_heroes(db)
    assert count >= 1
    async with db.conn.execute("SELECT COUNT(*) FROM heroes") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == count


async def test_sync_heroes_is_idempotent(db: Database) -> None:
    await sync_heroes(db)
    await sync_heroes(db)
    async with db.conn.execute("SELECT COUNT(*) FROM heroes") as cur:
        row = await cur.fetchone()
    assert row is not None
    specs = load_specs()
    assert row[0] == len(specs)


async def test_sync_heroes_updates_changed_field(db: Database) -> None:
    spec = HeroSpec(
        codename="testkin",
        name="Old Name",
        rarity="rare",
        element=None,
        house=None,
        terrain="forest",
        release_date=None,
        bonuses=("a",),
        tags=(),
    )
    await sync_heroes(db, [spec])
    updated = HeroSpec(**{**spec.__dict__, "name": "New Name", "bonuses": ("a", "b")})
    await sync_heroes(db, [updated])

    async with db.conn.execute(
        "SELECT name, bonuses_json FROM heroes WHERE codename = ?", ("testkin",)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["name"] == "New Name"
    assert json.loads(row["bonuses_json"]) == ["a", "b"]


async def test_owned_hero_unique_dupes_increment(db: Database) -> None:
    await sync_heroes(db)
    await db.get_or_create_player(99)
    async with db.conn.execute("SELECT id FROM heroes LIMIT 1") as cur:
        hero_row = await cur.fetchone()
    assert hero_row is not None
    hero_id = hero_row["id"]

    for _ in range(3):
        await db.conn.execute(
            """
            INSERT INTO owned_heroes (discord_user_id, hero_id, level)
            VALUES (?, ?, 1)
            ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                dupes_pending = dupes_pending + 1
            """,
            (99, hero_id),
        )
    await db.conn.commit()

    async with db.conn.execute(
        "SELECT level, dupes_pending FROM owned_heroes WHERE discord_user_id = ? AND hero_id = ?",
        (99, hero_id),
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["level"] == 1
    assert row["dupes_pending"] == 2


async def test_seed_path_resolves() -> None:
    assert HEROES_JSON_PATH.exists(), "heroes.json must ship with the package"
