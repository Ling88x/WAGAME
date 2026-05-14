"""Pure logic for the Witch's Vault — daily open + streak loot box.

One open per reset day (21:00 UTC). Streak tier upgrades both the RNG
weights and a flat reward multiplier — the longer the chain, the more
expensive a skipped day feels. Milestone bonuses fire once each on the
exact day the streak crosses 7 / 14 / 30, adding gems + shards on top
of the regular bag.

The cog asks for `today_iso` and a seedable `rng`; everything below is
deterministic and testable without spinning up a bot.
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass

# Streak thresholds where weights and the bonus multiplier step up.
STREAK_BREAKPOINTS: tuple[tuple[int, float], ...] = (
    (30, 2.00),
    (14, 1.50),
    (7,  1.25),
    (4,  1.10),
    (0,  1.00),
)

# Day numbers that pay an extra one-shot milestone bonus.
MILESTONE_DAYS: dict[int, tuple[int, int]] = {
    # day -> (gems, random_shards)
    7:  (100, 5),
    14: (250, 10),
    30: (500, 20),
}


@dataclass(frozen=True)
class RewardBag:
    tier: str
    gold: int
    food: int
    wood: int
    gems: int
    shard_count: int       # only set when tier == "mythic"
    milestone_gems: int    # extra gems from hitting day 7 / 14 / 30
    milestone_shards: int  # extra shards from milestone


# Base bag per tier. RSS is the per-resource amount (so a "common" bag
# of 10_000 RSS pays 10k gold + 10k food + 10k wood).
_BASE_BAGS: dict[str, dict[str, int]] = {
    "common":    {"rss": 10_000,    "gems": 0},
    "uncommon":  {"rss": 50_000,    "gems": 25},
    "rare":      {"rss": 200_000,   "gems": 100},
    "legendary": {"rss": 1_000_000, "gems": 500},
    "mythic":    {"rss": 2_000_000, "gems": 1500},
}

# Tier weights as a function of streak tier. Floor of 7 → uncommon
# minimum (no commons), 14 → rare minimum, 30 → legendary minimum.
_WEIGHTS_DEFAULT: list[tuple[str, int]] = [
    ("common",    50),
    ("uncommon",  30),
    ("rare",      15),
    ("legendary",  4),
    ("mythic",     1),
]
_WEIGHTS_DAY_7: list[tuple[str, int]] = [
    ("uncommon",  50),
    ("rare",      35),
    ("legendary", 13),
    ("mythic",     2),
]
_WEIGHTS_DAY_14: list[tuple[str, int]] = [
    ("rare",      55),
    ("legendary", 35),
    ("mythic",    10),
]
_WEIGHTS_DAY_30: list[tuple[str, int]] = [
    ("legendary", 70),
    ("mythic",    30),
]

# Shard count range on a mythic bag.
MYTHIC_SHARDS_MIN = 10
MYTHIC_SHARDS_MAX = 30


# -- streak math --------------------------------------------------------


def compute_next_streak(
    previous_streak: int,
    previous_date_iso: str | None,
    today_iso: str,
) -> tuple[int, bool]:
    """Decide the new streak after a potential open today.

    Returns `(new_streak, already_opened_today)`. If the player already
    opened today, returns the previous streak unchanged with the flag set.
    Otherwise: continues the chain if previous_date is exactly yesterday,
    starts a fresh chain at 1 in every other case (first open ever,
    skipped a day, or clock skew).
    """
    if previous_date_iso == today_iso:
        return previous_streak, True
    today = datetime.date.fromisoformat(today_iso)
    if previous_date_iso is None:
        return 1, False
    try:
        previous = datetime.date.fromisoformat(previous_date_iso)
    except ValueError:
        return 1, False
    delta = (today - previous).days
    if delta == 1:
        return previous_streak + 1, False
    # Skip / clock skew / replay: chain breaks.
    return 1, False


def streak_multiplier(streak: int) -> float:
    """Flat multiplier on the RSS/gems totals based on streak."""
    for floor, mul in STREAK_BREAKPOINTS:
        if streak >= floor:
            return mul
    return 1.0


def _weights_for_streak(streak: int) -> list[tuple[str, int]]:
    if streak >= 30:
        return _WEIGHTS_DAY_30
    if streak >= 14:
        return _WEIGHTS_DAY_14
    if streak >= 7:
        return _WEIGHTS_DAY_7
    return _WEIGHTS_DEFAULT


# -- reward roll --------------------------------------------------------


def roll_reward(
    streak: int,
    *,
    rng: random.Random | None = None,
) -> RewardBag:
    """Pick a tier given the streak, apply the streak multiplier, attach
    milestone bonus if today is a milestone day."""
    if streak < 1:
        raise ValueError("streak must be >= 1 once the open is committed")
    r = rng or random
    weights = _weights_for_streak(streak)
    tier = r.choices(
        [t for t, _ in weights],
        weights=[w for _, w in weights],
    )[0]

    base = _BASE_BAGS[tier]
    mul = streak_multiplier(streak)
    rss = int(base["rss"] * mul)
    gems = int(base["gems"] * mul)

    shard_count = (
        r.randint(MYTHIC_SHARDS_MIN, MYTHIC_SHARDS_MAX)
        if tier == "mythic"
        else 0
    )

    milestone = MILESTONE_DAYS.get(streak, (0, 0))
    return RewardBag(
        tier=tier,
        gold=rss,
        food=rss,
        wood=rss,
        gems=gems,
        shard_count=shard_count,
        milestone_gems=milestone[0],
        milestone_shards=milestone[1],
    )


# -- reset-day timestamp -----------------------------------------------


def next_reset_unix(
    now: datetime.datetime | None = None,
    reset_hour_utc: int = 21,
) -> int:
    """Unix timestamp of the next 21:00 UTC tick. Used for `<t:R>` countdowns."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    today_reset = now.replace(
        hour=reset_hour_utc, minute=0, second=0, microsecond=0
    )
    if now >= today_reset:
        today_reset += datetime.timedelta(days=1)
    return int(today_reset.timestamp())
