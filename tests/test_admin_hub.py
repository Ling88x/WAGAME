"""Tests for the admin hub render and starter-hero grant."""

from __future__ import annotations

from pathlib import Path

import pytest

from wagame.cogs.admin import (
    _lookup_hero_id,
    _parse_int,
    _parse_target_id,
    render_admin_hub_embed,
)
from wagame.db import Database
from wagame.heroes_data import sync_heroes
from wagame.troops_data import sync_troops


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "admin.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    await sync_troops(database)
    yield database
    await database.close()


# -- embed --------------------------------------------------------------


def test_admin_hub_embed_has_action_groups() -> None:
    embed = render_admin_hub_embed()
    names = [f.name for f in embed.fields]
    assert "Resources" in names
    assert "Combat / Economy" in names
    assert "Danger" in names


# -- helpers ------------------------------------------------------------


def test_parse_int_defaults_empty() -> None:
    assert _parse_int("", 7) == 7
    assert _parse_int("  ", 7) == 7
    assert _parse_int("42") == 42


def test_parse_int_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        _parse_int("not-a-number")


def test_parse_target_id_falls_back_to_self() -> None:
    assert _parse_target_id("", 999) == 999
    assert _parse_target_id("   ", 999) == 999


def test_parse_target_id_strips_mention_markers() -> None:
    assert _parse_target_id("<@123456>", 0) == 123456
    assert _parse_target_id("<@!123456>", 0) == 123456
    assert _parse_target_id("123456", 0) == 123456


async def test_lookup_hero_id_by_name(db: Database) -> None:
    hit = await _lookup_hero_id(db, "Vivienne Corvelia")
    assert hit is not None
    _, name = hit
    assert name == "Vivienne Corvelia"


async def test_lookup_hero_id_by_codename(db: Database) -> None:
    hit = await _lookup_hero_id(db, "vivi")
    assert hit is not None


async def test_lookup_hero_id_misses_return_none(db: Database) -> None:
    assert await _lookup_hero_id(db, "doesnotexist") is None


# -- starter Vivienne ---------------------------------------------------


async def test_new_player_receives_starter_vivienne(db: Database) -> None:
    await db.get_or_create_player(1)
    async with db.conn.execute(
        "SELECT h.codename FROM owned_heroes o JOIN heroes h ON h.id = o.hero_id "
        "WHERE o.discord_user_id = 1"
    ) as cur:
        rows = await cur.fetchall()
    codenames = [r["codename"] for r in rows]
    assert "vivi" in codenames


async def test_repeated_get_or_create_does_not_duplicate_starter(db: Database) -> None:
    for _ in range(3):
        await db.get_or_create_player(1)
    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM owned_heroes WHERE discord_user_id = 1"
    ) as cur:
        n = int((await cur.fetchone())["n"])
    assert n == 1  # Starter only, no duplicates.
