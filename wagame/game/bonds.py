"""Hero Bonds — pure math for pair-level affinity.

A player builds bond between two heroes by riding them together in a
hunt march. Points accumulate (1 per chip, 3 per kill, multiplied by an
affinity bonus when the pair shares terrain / house / element).

Bond levels are thresholds on the running points total. Each level
adds a flat percent to march power when those two heroes march
together — applied as an extra multiplier alongside command + research
in the combat formula.

Identity convention: `(hero_a, hero_b)` is canonicalised so
hero_a_id < hero_b_id. Callers should pass the pair through
`canonical_pair` before hitting the DB.
"""

from __future__ import annotations

from dataclasses import dataclass

# Points awarded per engagement.
POINTS_CHIP = 1
POINTS_KILL = 3

# Affinity multipliers — stack when multiple traits match.
AFFINITY_TERRAIN = 1.5
AFFINITY_HOUSE = 2.0
AFFINITY_ELEMENT = 1.3

# Bond level thresholds (points needed to reach that level).
LEVEL_THRESHOLDS: tuple[tuple[int, int, int], ...] = (
    # (level, points_required, march_power_bonus_pct)
    (4, 500,  35),
    (3, 150,  20),
    (2, 50,   10),
    (1, 10,   5),
    (0, 0,    0),
)


@dataclass(frozen=True)
class BondTier:
    level: int
    bonus_pct: int


@dataclass(frozen=True)
class BondLine:
    hero_a_id: int
    hero_a_name: str
    hero_b_id: int
    hero_b_name: str
    points: int
    level: int
    bonus_pct: int
    last_bonded_at: int


def canonical_pair(hero_a_id: int, hero_b_id: int) -> tuple[int, int]:
    if hero_a_id == hero_b_id:
        raise ValueError("bond pair must be two different heroes")
    a, b = sorted((hero_a_id, hero_b_id))
    return a, b


def tier_for(points: int) -> BondTier:
    """Pick the highest tier whose threshold is met."""
    for level, threshold, bonus in LEVEL_THRESHOLDS:
        if points >= threshold:
            return BondTier(level=level, bonus_pct=bonus)
    return BondTier(level=0, bonus_pct=0)


def points_for_engagement(killed: bool) -> int:
    return POINTS_KILL if killed else POINTS_CHIP


def affinity_multiplier(
    terrain_a: str | None, terrain_b: str | None,
    house_a: str | None, house_b: str | None,
    element_a: str | None, element_b: str | None,
) -> float:
    """Stack affinity bonuses from shared traits.

    Nulls never match — only populated trait equality boosts growth.
    """
    mult = 1.0
    if terrain_a and terrain_a == terrain_b:
        mult *= AFFINITY_TERRAIN
    if house_a and house_a == house_b:
        mult *= AFFINITY_HOUSE
    if element_a and element_a == element_b:
        mult *= AFFINITY_ELEMENT
    return mult


def next_threshold(points: int) -> tuple[int, int] | None:
    """For UI hints. Returns `(next_level, points_to_reach)` or None at top tier."""
    for level, threshold, _ in reversed(LEVEL_THRESHOLDS):
        if points < threshold:
            return level, threshold - points
    return None
