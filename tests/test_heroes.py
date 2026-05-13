"""Tests for the heroes loader and owned_heroes invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wagame.cogs.heroes import (
    HEROES_PER_PAGE,
    _autocomplete_hero,
    _build_page_embeds,
    _hero_card_embed,
    _hero_detail_embed,
)
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
        image_url=None,
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


# -- image_url plumbing ----------------------------------------------------


async def test_sync_heroes_persists_image_url(db: Database) -> None:
    spec = HeroSpec(
        codename="pixel",
        name="Pixel",
        rarity="epic",
        element=None,
        house=None,
        terrain="forest",
        release_date=None,
        image_url="https://example.com/p.png",
        bonuses=(),
        tags=(),
    )
    await sync_heroes(db, [spec])
    async with db.conn.execute(
        "SELECT image_url FROM heroes WHERE codename = ?", ("pixel",)
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
    assert row["image_url"] == "https://example.com/p.png"


# -- autocomplete ----------------------------------------------------------


async def test_autocomplete_matches_codename_substring(db: Database) -> None:
    await sync_heroes(
        db,
        [
            HeroSpec(
                codename="ghostpink",
                name="Roselda Graves",
                rarity="rare",
                element=None,
                house=None,
                terrain=None,
                release_date=None,
                image_url=None,
                bonuses=(),
                tags=(),
            ),
            HeroSpec(
                codename="waterlava",
                name="Lyria Ignis",
                rarity="rare",
                element=None,
                house=None,
                terrain=None,
                release_date=None,
                image_url=None,
                bonuses=(),
                tags=(),
            ),
        ],
    )
    choices = await _autocomplete_hero(db, "lava")
    assert len(choices) == 1
    assert choices[0].value == "waterlava"
    assert "Lyria Ignis" in choices[0].name


async def test_autocomplete_matches_display_name_substring(db: Database) -> None:
    await sync_heroes(
        db,
        [
            HeroSpec(
                codename="ghostpink",
                name="Roselda Graves",
                rarity="rare",
                element=None,
                house=None,
                terrain=None,
                release_date=None,
                image_url=None,
                bonuses=(),
                tags=(),
            ),
        ],
    )
    choices = await _autocomplete_hero(db, "rose")
    assert any(c.value == "ghostpink" for c in choices)


async def test_autocomplete_orders_mythic_before_rare(db: Database) -> None:
    base = {
        "element": None,
        "house": None,
        "terrain": None,
        "release_date": None,
        "image_url": None,
        "bonuses": (),
        "tags": (),
    }
    await sync_heroes(
        db,
        [
            HeroSpec(codename="aa", name="Alpha Match", rarity="rare", **base),
            HeroSpec(codename="bb", name="Beta Match", rarity="mythic", **base),
        ],
    )
    choices = await _autocomplete_hero(db, "match")
    assert [c.value for c in choices] == ["bb", "aa"]


async def test_autocomplete_empty_query_returns_some_heroes(db: Database) -> None:
    await sync_heroes(db)
    choices = await _autocomplete_hero(db, "", limit=5)
    assert 1 <= len(choices) <= 5


# -- grid rendering --------------------------------------------------------


class _Row(dict):
    """sqlite3.Row stand-in: dict + __getitem__ is enough for our renderers."""


def _row(**overrides) -> _Row:
    defaults = {
        "codename": "ghostpink",
        "name": "Roselda Graves",
        "rarity": "epic",
        "house": None,
        "terrain": "forest",
        "image_url": None,
        "level": 1,
        "dupes_pending": 0,
        "element": None,
        "release_date": "2026-02",
        "bonuses_json": "[]",
        "tags_json": "[]",
    }
    defaults.update(overrides)
    return _Row(**defaults)


def test_hero_card_embed_uses_rarity_color() -> None:
    embed = _hero_card_embed(_row(rarity="legendary"))
    assert embed.color is not None
    # discord.Color.gold is the rarity color we wired up.
    import discord

    assert embed.color.value == discord.Color.gold().value


def test_hero_card_embed_sets_thumbnail_when_image_present() -> None:
    embed = _hero_card_embed(_row(image_url="https://x/y.png"))
    assert embed.thumbnail.url == "https://x/y.png"


def test_hero_card_embed_skips_thumbnail_when_missing() -> None:
    embed = _hero_card_embed(_row(image_url=None))
    # discord-py returns an empty thumbnail object; .url is None.
    assert embed.thumbnail.url is None


def test_build_page_embeds_caps_at_one_plus_nine() -> None:
    class _User:
        display_name = "Tester"
        display_avatar = type("A", (), {"url": "https://x/avatar.png"})()

    rows = [_row(name=f"Hero {i}", codename=f"h{i}") for i in range(15)]
    page0 = _build_page_embeds(_User(), rows, page=0)
    assert len(page0) == 1 + HEROES_PER_PAGE  # summary + cards
    page1 = _build_page_embeds(_User(), rows, page=1)
    assert len(page1) == 1 + (15 - HEROES_PER_PAGE)


def test_hero_detail_embed_renders_bonuses_and_thumbnail() -> None:
    row = _row(
        bonuses_json='["Bonus A", "Bonus B"]',
        tags_json='["cool"]',
        image_url="https://x/y.png",
    )
    embed = _hero_detail_embed(row)
    assert embed.thumbnail.url == "https://x/y.png"
    bonus_field = next(f for f in embed.fields if f.name == "Bonuses")
    assert "Bonus A" in bonus_field.value
    assert "Bonus B" in bonus_field.value
