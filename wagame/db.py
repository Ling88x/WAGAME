"""Async SQLite access + migration runner."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_FILENAME_RE = re.compile(r"^(\d{3,})_[\w-]+\.sql$")


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn

    async def migrate(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY,"
            "applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        await self.conn.commit()

        async with self.conn.execute("SELECT MAX(version) FROM schema_version") as cur:
            row = await cur.fetchone()
        current = row[0] or 0

        for version, path in _discover_migrations():
            if version <= current:
                continue
            log.info("Applying migration %03d (%s)", version, path.name)
            sql = path.read_text(encoding="utf-8")
            await self.conn.executescript(sql)
            await self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            await self.conn.commit()

    async def get_or_create_player(self, discord_user_id: int) -> aiosqlite.Row:
        await self.conn.execute(
            "INSERT OR IGNORE INTO players (discord_user_id) VALUES (?)",
            (discord_user_id,),
        )
        await self.conn.execute(
            "UPDATE players SET last_seen = datetime('now') WHERE discord_user_id = ?",
            (discord_user_id,),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT * FROM players WHERE discord_user_id = ?", (discord_user_id,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        return row


def _discover_migrations() -> list[tuple[int, Path]]:
    if not MIGRATIONS_DIR.exists():
        return []
    found: list[tuple[int, Path]] = []
    for path in MIGRATIONS_DIR.iterdir():
        if not path.is_file():
            continue
        match = MIGRATION_FILENAME_RE.match(path.name)
        if not match:
            continue
        found.append((int(match.group(1)), path))
    found.sort(key=lambda item: item[0])
    return found
