"""Pure logic for the gathering system — no Discord, no DB.

Time, RNG and persistence are injected so the rules are easy to unit-test.
Tuning values live here as module constants; the balance pass later will
likely move them into a config file or per-resource node table.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

Resource = Literal["gold", "food", "wood"]
RESOURCES: tuple[Resource, ...] = ("gold", "food", "wood")

# Slot rules — capacity progresses via research, capped at MAX_SLOTS.
DEFAULT_SLOTS = 2
MAX_SLOTS = 6

# Time spent on a single gather, identical for every resource. Tune in balance
# pass. Stored separately so tests can monkeypatch without touching production.
GATHER_DURATION_SECONDS = 30 * 60

# Base yield per gather, inclusive range. Gold deposits ("żyły złota") are
# slightly leaner than food/wood at the same time cost — see CLAUDE.md
# clarification with the user.
BASE_YIELD: dict[Resource, tuple[int, int]] = {
    "gold": (400, 800),
    "food": (500, 1000),
    "wood": (500, 1000),
}

# Crit: rare bonus that doubles the rolled base yield.
CRIT_CHANCE = 0.05
CRIT_MULTIPLIER = 2


@dataclass(frozen=True)
class GatherRoll:
    resource: Resource
    base: int
    crit: bool

    @property
    def total(self) -> int:
        return self.base * CRIT_MULTIPLIER if self.crit else self.base


def roll_gather(resource: Resource, rng: random.Random | None = None) -> GatherRoll:
    """Decide base yield and crit at start time so the result is frozen on the row."""
    if resource not in BASE_YIELD:
        raise ValueError(f"Unknown resource: {resource!r}")
    r = rng or random
    lo, hi = BASE_YIELD[resource]
    base = r.randint(lo, hi)
    crit = r.random() < CRIT_CHANCE
    return GatherRoll(resource=resource, base=base, crit=crit)


def remaining_seconds(finishes_at: int, now: int) -> int:
    return max(0, finishes_at - now)


def is_finished(finishes_at: int, now: int) -> bool:
    return now >= finishes_at


def format_remaining(seconds: int) -> str:
    """Human-readable countdown — `12m 34s` / `1h 02m`. Empty string when done."""
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
