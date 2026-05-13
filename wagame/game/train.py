"""Pure logic for the Training Grounds — cost, time, claim resolution.

Schemaless types so the cog can pass either DB rows or dataclasses in tests.
Time and persistence stay in the cog; this module just does arithmetic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class TrainPlan:
    troop_codename: str
    count: int
    food_cost: int
    total_seconds: int


def food_cost(food_per_unit: int, count: int) -> int:
    return food_per_unit * count


def total_train_seconds(train_seconds_per_unit: int, count: int, speed_boost_pct: int) -> int:
    """Total wall-clock time for a batch, with the player's speed boost applied.

    `speed_boost_pct` is the reduction percentage (e.g. 10 → 10% faster).
    Capped at 99 to avoid a zero-or-negative duration when balance later
    pushes the curve up.
    """
    base = train_seconds_per_unit * max(0, count)
    pct = max(0, min(99, speed_boost_pct))
    return max(1, math.ceil(base * (100 - pct) / 100)) if base > 0 else 0


def plan_training(
    *,
    troop_codename: str,
    count: int,
    food_per_unit: int,
    train_seconds_per_unit: int,
    speed_boost_pct: int,
) -> TrainPlan:
    if count <= 0:
        raise ValueError("count must be > 0")
    return TrainPlan(
        troop_codename=troop_codename,
        count=count,
        food_cost=food_cost(food_per_unit, count),
        total_seconds=total_train_seconds(train_seconds_per_unit, count, speed_boost_pct),
    )


def max_affordable_count(
    *, food_available: int, food_per_unit: int, queue_cap: int
) -> int:
    """Largest batch the player can afford right now, capped by the queue."""
    if food_per_unit <= 0:
        return queue_cap
    by_food = food_available // food_per_unit
    return max(0, min(queue_cap, by_food))


def format_duration(seconds: int) -> str:
    """`12s`, `4m 03s`, `2h 15m`, `3d 04h`. Empty when zero or negative."""
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"
