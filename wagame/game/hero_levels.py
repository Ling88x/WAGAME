"""Hero leveling — pure math, no Discord or DB.

`owned_heroes` carries `level` (default 1) and `xp` (default 0, added in
migration 008). Cogs that award XP (`/hunt`, hero-assigned gathers) call
`apply_xp_gain` and write back `(level, xp)`.

Curve
-----
xp_to_next(L) = floor(100 * L ** 1.5).
- Lv 1 -> 2  = 100 XP
- Lv 10 -> 11 = ~3,162 XP
- Lv 29 -> 30 = ~15,608 XP
- Total Lv 1 -> 30 ~ 150k XP.

Stats
-----
Per level, additive:
- +5% effective ATK vs the rarity baseline.
- +2% hero march-speed bonus (stacks on top of research).

Hero `atk_base` isn't on the catalog yet, so we synthesize it from rarity
until per-hero stats land (see ROADMAP "Heroes element / house fields").

Cap
---
Hard-capped at STARTING_LEVEL_CAP (= 30). Hitting the cap with XP left
over banks the surplus on the row — when research raises the cap later,
banked XP pops level-ups on the next `apply_xp_gain` call.
"""

from __future__ import annotations

from dataclasses import dataclass

STARTING_LEVEL_CAP = 30
ATK_PCT_PER_LEVEL = 5
MARCH_SPEED_PCT_PER_LEVEL = 2

RARITY_BASE_ATK: dict[str, int] = {
    "mythic":    500,
    "legendary": 300,
    "epic":      200,
    "rare":      120,
    "uncommon":   70,
    "common":     40,
}


def xp_to_next(level: int) -> int:
    """XP needed to advance from `level` to `level + 1`."""
    if level < 1:
        raise ValueError(f"level must be >= 1, got {level}")
    return int(100 * (level ** 1.5))


def base_atk_for_rarity(rarity: str | None) -> int:
    if rarity is None:
        return RARITY_BASE_ATK["common"]
    return RARITY_BASE_ATK.get(rarity.lower(), RARITY_BASE_ATK["common"])


def atk_eff(rarity: str | None, level: int) -> int:
    """Effective hero ATK at `level` given rarity baseline."""
    if level < 1:
        raise ValueError(f"level must be >= 1, got {level}")
    base = base_atk_for_rarity(rarity)
    multiplier = 1.0 + (level - 1) * ATK_PCT_PER_LEVEL / 100.0
    return round(base * multiplier)


def march_speed_pct(level: int) -> int:
    """Per-hero march-speed bonus (percent points), additive vs research."""
    if level < 1:
        raise ValueError(f"level must be >= 1, got {level}")
    return (level - 1) * MARCH_SPEED_PCT_PER_LEVEL


@dataclass(frozen=True)
class LevelUpResult:
    new_level: int
    new_xp: int
    levels_gained: int


def apply_xp_gain(
    level: int,
    xp: int,
    gained: int,
    cap: int = STARTING_LEVEL_CAP,
) -> LevelUpResult:
    """Cascade level-ups for a hero given current state and an XP grant.

    Excess XP at cap stays on `xp` so a future cap raise can consume it.
    """
    if level < 1:
        raise ValueError(f"level must be >= 1, got {level}")
    if xp < 0:
        raise ValueError(f"xp must be >= 0, got {xp}")
    if gained < 0:
        raise ValueError(f"gained must be >= 0, got {gained}")

    starting = level
    xp += gained
    while level < cap:
        need = xp_to_next(level)
        if xp < need:
            break
        xp -= need
        level += 1
    return LevelUpResult(new_level=level, new_xp=xp, levels_gained=level - starting)
