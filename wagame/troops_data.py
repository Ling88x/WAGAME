"""Troop catalog: 8 in-game units across 4 tiers, synced into `troops` on boot.

Stats are rounded from the in-game Summoning Tower screens (see
docs/ROADMAP.md). Cost and training time are bot placeholders — balance
pass once research / combat / raids are wired in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from wagame.db import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TroopSpec:
    codename: str
    name: str
    tier: int
    role: str | None         # 'monster' | 'defender' | 'player' | None for generalists
    march_speed: int
    carry_cap: float
    attack: int
    hp: int
    role_bonus: int          # +100 / +150 / 0
    food_per_unit: int
    train_seconds: int


# Order matters only for display fallbacks — UI sorts by (tier, name).
TROOPS: tuple[TroopSpec, ...] = (
    TroopSpec("catsith",  "Catsith",  1, None,       40,  1.00,  60,  55,   0,  15,    30),
    TroopSpec("gryphon",  "Gryphon",  2, None,       40,  1.00, 140, 140,   0,  50,   120),
    TroopSpec("dratsie",  "Dratsie",  3, "monster",  72,  3.00,  76, 199, 100, 120,   600),
    TroopSpec("pangolin", "Pangolin", 3, "defender", 64,  1.00,  76, 199, 100, 120,   600),
    TroopSpec("musjay",   "Musjay",   3, "player",   71,  1.42,  76, 199, 100, 120,   600),
    TroopSpec("dawon",    "Dawon",    4, "monster", 113,  3.00, 200, 500, 150, 300,  3600),
    TroopSpec("shishi",   "Shishi",   4, "defender",101,  1.00, 200, 500, 150, 300,  3600),
    TroopSpec("kelpie",   "Kelpie",   4, "player",  119,  1.42, 200, 500, 150, 300,  3600),
)


async def sync_troops(db: Database, specs: tuple[TroopSpec, ...] = TROOPS) -> int:
    """Upsert troop specs into the `troops` table. Returns number of rows touched."""
    rows = 0
    for spec in specs:
        await db.conn.execute(
            """
            INSERT INTO troops (codename, name, tier, role, march_speed, carry_cap,
                                attack, hp, role_bonus, food_per_unit, train_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(codename) DO UPDATE SET
                name          = excluded.name,
                tier          = excluded.tier,
                role          = excluded.role,
                march_speed   = excluded.march_speed,
                carry_cap     = excluded.carry_cap,
                attack        = excluded.attack,
                hp            = excluded.hp,
                role_bonus    = excluded.role_bonus,
                food_per_unit = excluded.food_per_unit,
                train_seconds = excluded.train_seconds
            """,
            (
                spec.codename,
                spec.name,
                spec.tier,
                spec.role,
                spec.march_speed,
                spec.carry_cap,
                spec.attack,
                spec.hp,
                spec.role_bonus,
                spec.food_per_unit,
                spec.train_seconds,
            ),
        )
        rows += 1
    await db.conn.commit()
    log.info("Synced %d troop(s).", rows)
    return rows
