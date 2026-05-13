"""Heroes catalog: load `data/heroes.json` and sync it into the `heroes` table.

The JSON file is the source of truth for the static roster (rarity, element,
house, terrain, bonuses). Player-owned data lives in `owned_heroes` and is
unaffected by sync.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from wagame.db import Database

log = logging.getLogger(__name__)

HEROES_JSON_PATH = Path(__file__).resolve().parent / "data" / "heroes.json"

KNOWN_RARITIES = ("common", "uncommon", "rare", "epic", "legendary", "mythic")


@dataclass(frozen=True)
class HeroSpec:
    codename: str
    name: str
    rarity: str
    element: str | None
    house: str | None
    terrain: str | None
    release_date: str | None
    image_url: str | None
    bonuses: tuple[str, ...]
    tags: tuple[str, ...]


def load_specs(path: Path = HEROES_JSON_PATH) -> list[HeroSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    specs: list[HeroSpec] = []
    seen: set[str] = set()
    for entry in raw.get("heroes", []):
        codename = str(entry["codename"]).strip().lower()
        if not codename:
            raise ValueError(f"Hero entry missing codename: {entry!r}")
        if codename in seen:
            raise ValueError(f"Duplicate codename in heroes.json: {codename}")
        seen.add(codename)

        rarity = str(entry["rarity"]).strip().lower()
        if rarity not in KNOWN_RARITIES:
            log.warning("Hero %s has unknown rarity %r (allowed: %s)", codename, rarity,
                        ", ".join(KNOWN_RARITIES))

        specs.append(
            HeroSpec(
                codename=codename,
                name=str(entry["name"]).strip(),
                rarity=rarity,
                element=_opt_str(entry.get("element")),
                house=_opt_str(entry.get("house")),
                terrain=_opt_str(entry.get("terrain")),
                release_date=_opt_str(entry.get("release_date")),
                image_url=_opt_str(entry.get("image_url")),
                bonuses=tuple(str(b) for b in entry.get("bonuses", [])),
                tags=tuple(str(t) for t in entry.get("tags", [])),
            )
        )
    return specs


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def sync_heroes(db: Database, specs: list[HeroSpec] | None = None) -> int:
    """Upsert hero specs into the `heroes` table. Returns number of rows touched."""
    if specs is None:
        specs = load_specs()

    rows = 0
    for spec in specs:
        await db.conn.execute(
            """
            INSERT INTO heroes (codename, name, rarity, element, house, terrain,
                                release_date, image_url, bonuses_json, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codename) DO UPDATE SET
                name         = excluded.name,
                rarity       = excluded.rarity,
                element      = excluded.element,
                house        = excluded.house,
                terrain      = excluded.terrain,
                release_date = excluded.release_date,
                image_url    = excluded.image_url,
                bonuses_json = excluded.bonuses_json,
                tags_json    = excluded.tags_json
            """,
            (
                spec.codename,
                spec.name,
                spec.rarity,
                spec.element,
                spec.house,
                spec.terrain,
                spec.release_date,
                spec.image_url,
                json.dumps(list(spec.bonuses)),
                json.dumps(list(spec.tags)),
            ),
        )
        rows += 1
    await db.conn.commit()
    log.info("Synced %d hero(es) from %s", rows, HEROES_JSON_PATH.name)
    return rows
