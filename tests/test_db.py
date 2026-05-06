"""Smoke tests for the migration runner and player creation."""

from __future__ import annotations

from pathlib import Path

import pytest

from wagame.db import Database


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def test_migration_records_version(db: Database) -> None:
    async with db.conn.execute("SELECT MAX(version) FROM schema_version") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] >= 1


async def test_migration_is_idempotent(db: Database) -> None:
    await db.migrate()
    async with db.conn.execute("SELECT COUNT(*) FROM schema_version") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


async def test_get_or_create_player_creates_row(db: Database) -> None:
    player = await db.get_or_create_player(123456)
    assert player["discord_user_id"] == 123456
    assert player["gold"] == 0
    assert player["player_level"] == 1
    assert player["march_capacity"] == 1


async def test_get_or_create_player_is_idempotent(db: Database) -> None:
    first = await db.get_or_create_player(42)
    second = await db.get_or_create_player(42)
    assert first["created_at"] == second["created_at"]
    async with db.conn.execute("SELECT COUNT(*) FROM players") as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1
