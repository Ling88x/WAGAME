"""Player-level XP — separate from per-hero levels.

Curve mirrors hero_levels (`100 * L^1.5` per level), but the cap is
softer (PLAYER_LEVEL_CAP = 100) and excess XP banks at cap so future
raises don't waste progress. Level titles map ranges to flavor labels
shown in profile / hub.

Earned through play:
- Hunt kills: 10 per mob_lv
- Sighting kills: 50 + (10 per mob_lv)
- Gather claims: 50 per claimed march
- Daily quest claim: 200
- Arena: 100 win / 25 loss / 50 draw
- Council qualifying contribution: 500

Level-up reward (per level): 25 gems + 1 random hero shard.
"""

from __future__ import annotations

from dataclasses import dataclass

PLAYER_LEVEL_CAP = 100
PLAYER_LEVELUP_GEMS = 25
PLAYER_LEVELUP_SHARDS = 1


def xp_to_next(level: int) -> int:
    """XP needed to advance from `level` to `level + 1`. Same shape as
    hero_levels.xp_to_next so growth feels familiar."""
    if level < 1:
        raise ValueError(f"level must be >= 1, got {level}")
    return int(100 * (level ** 1.5))


@dataclass(frozen=True)
class PlayerXPResult:
    new_level: int
    new_xp: int
    levels_gained: int


def apply_player_xp(
    level: int,
    xp: int,
    gained: int,
    cap: int = PLAYER_LEVEL_CAP,
) -> PlayerXPResult:
    """Cascade level-ups given starting state + an XP grant.

    Excess XP at cap stays banked. Mirrors hero_levels.apply_xp_gain
    so the semantics are identical.
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
    return PlayerXPResult(
        new_level=level, new_xp=xp, levels_gained=level - starting
    )


TITLE_BANDS: tuple[tuple[int, str], ...] = (
    (50, "Archmage"),
    (25, "Mage"),
    (10, "Adept"),
    (5,  "Apprentice"),
    (1,  "Novice"),
)


def title_for(level: int) -> str:
    for floor, name in TITLE_BANDS:
        if level >= floor:
            return name
    return "Novice"
