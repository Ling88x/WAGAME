"""Tests for the Bestiary — pure formatting + DB-backed record/fetch."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from wagame.cogs.bestiary import (
    documented_count,
    fetch_lines,
    record_encounter,
)
from wagame.db import Database
from wagame.game.bestiary import (
    TENEBRAL_SLOT_COUNT,
    BestiaryLine,
    completion_pct,
    render_line,
)


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    from wagame.heroes_data import sync_heroes
    database = Database(tmp_path / "bestiary.db")
    await database.connect()
    await database.migrate()
    await sync_heroes(database)
    yield database
    await database.close()


# -- pure logic ----------------------------------------------------------


def test_slot_count_matches_tenebral_table() -> None:
    from wagame.game.hunt import TENEBRAL_TABLE
    assert len(TENEBRAL_TABLE) == TENEBRAL_SLOT_COUNT


def test_completion_pct_floors_and_caps() -> None:
    assert completion_pct(0, 12) == 0
    assert completion_pct(6, 12) == 50
    assert completion_pct(12, 12) == 100
    assert completion_pct(7, 0) == 0  # defensive


def test_render_line_undocumented_uses_question_marks() -> None:
    line = BestiaryLine(
        level=5, name="Void Cervus",
        slain=0, encounters=0,
        first_seen_at=0, last_seen_at=0,
    )
    text = render_line(line)
    assert "???" in text
    assert "Void Cervus" not in text  # name hidden until seen


def test_render_line_encountered_but_never_slain() -> None:
    line = BestiaryLine(
        level=3, name="Void Lupus",
        slain=0, encounters=2,
        first_seen_at=100, last_seen_at=200,
    )
    text = render_line(line)
    assert "Void Lupus" in text
    assert "never slain" in text


def test_render_line_slain_shows_count_and_timestamp() -> None:
    line = BestiaryLine(
        level=1, name="Void Cervus",
        slain=4, encounters=10,
        first_seen_at=100, last_seen_at=200,
    )
    text = render_line(line)
    assert "Void Cervus" in text
    assert "slain **4**x" in text
    assert "<t:200:R>" in text


# -- DB-backed -----------------------------------------------------------


async def test_record_encounter_inserts_with_kill(db: Database) -> None:
    await db.get_or_create_player(1)
    await record_encounter(
        db, user_id=1, mob_kind="tenebral", mob_level=3,
        killed=True, now=1_000,
    )
    async with db.conn.execute(
        "SELECT slain_count, encounter_count, first_seen_at, last_seen_at "
        "FROM bestiary_entries WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert int(row["slain_count"]) == 1
    assert int(row["encounter_count"]) == 1
    assert int(row["first_seen_at"]) == 1_000
    assert int(row["last_seen_at"]) == 1_000


async def test_record_encounter_chip_then_kill_accumulates(db: Database) -> None:
    await db.get_or_create_player(1)
    await record_encounter(
        db, user_id=1, mob_kind="tenebral", mob_level=3,
        killed=False, now=1_000,
    )
    await record_encounter(
        db, user_id=1, mob_kind="tenebral", mob_level=3,
        killed=True, now=2_000,
    )
    async with db.conn.execute(
        "SELECT slain_count, encounter_count, first_seen_at, last_seen_at "
        "FROM bestiary_entries WHERE discord_user_id = 1"
    ) as cur:
        row = await cur.fetchone()
    assert int(row["slain_count"]) == 1
    assert int(row["encounter_count"]) == 2
    assert int(row["first_seen_at"]) == 1_000
    assert int(row["last_seen_at"]) == 2_000


async def test_fetch_lines_fills_all_twelve_slots(db: Database) -> None:
    await db.get_or_create_player(1)
    await record_encounter(
        db, user_id=1, mob_kind="tenebral", mob_level=4,
        killed=True, now=int(time.time()),
    )
    lines = await fetch_lines(db, 1)
    assert len(lines) == TENEBRAL_SLOT_COUNT
    assert {line.level for line in lines} == set(range(1, 13))
    by_level = {line.level: line for line in lines}
    assert by_level[4].documented is True
    assert by_level[4].slain == 1
    assert by_level[5].documented is False


async def test_documented_count_reflects_only_seen_levels(db: Database) -> None:
    await db.get_or_create_player(1)
    for level in (1, 2, 5):
        await record_encounter(
            db, user_id=1, mob_kind="tenebral", mob_level=level,
            killed=False, now=int(time.time()),
        )
    assert await documented_count(db, 1) == 3
