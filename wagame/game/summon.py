"""Hero summoning math — cost per rarity, shard roll curve, pity logic.

Pure functions, no DB. The cog layer (wagame/cogs/summon.py) wraps this
in the panel + slash command. Testable in isolation via a seeded
`Random`.

Curve shape (tuned to "between steep and middle"): mean is ~20 shards
per summon, so unlocks land around 5 summons of natural luck. 1% chance
per summon to jackpot directly into 100+ shards. Pity guarantees an
unlock no later than the 100th summon on the same hero.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Costs are keyed by rarity. The user pinned legendary/epic/rare; the others
# fall back on the same slope (uncommon a notch cheaper than rare; mythic
# meaningfully steeper than legendary).
SUMMON_COST: dict[str, int] = {
    "common":    400,
    "uncommon":  500,
    "rare":      800,
    "epic":      900,
    "legendary": 2000,
    "mythic":    5000,
}

# Shard count to unlock a hero. Beyond this, shards keep accumulating in
# hero_shards.count for future level upgrades (separate PR).
SHARDS_PER_UNLOCK = 100

# Hard pity: if a player makes this many summons on one hero without
# unlocking, the next summon is topped up to push them past
# SHARDS_PER_UNLOCK. Combined with the minimum-1-shard floor, every hero
# unlocks in ≤100 summons.
PITY_LIMIT = 100

# Daily gem bonus, auto-claimed on the first interaction after the WA reset
# boundary (21:00 UTC). The actual boundary lives in wagame/game/daily.py.
DAILY_GEM_BONUS = 100

# Onboarding grant. Mirrored in migration 007 for existing accounts; new
# accounts get this via the column DEFAULT.
STARTING_GEMS = 5000


# (low, high, weight). Sum of weights doesn't have to be 1 — we normalize.
# Means: 5, 19.5, 54.5, 89.5, 150. Weighted mean ≈ 19.8 shards/summon.
SHARD_BANDS: tuple[tuple[int, int, float], ...] = (
    (1,   9,    0.55),
    (10,  29,   0.28),
    (30,  79,   0.13),
    (80,  99,   0.03),
    (100, 200,  0.01),
)


@dataclass(frozen=True)
class SummonResult:
    """Outcome of a single summon on one hero, ready for the cog to display."""

    shards_rolled: int       # Shards actually credited (after pity top-up).
    shards_rolled_natural: int  # What the dice said, before pity intervened.
    new_total: int           # Player's cumulative shard count after this summon.
    new_pity: int            # pity value to persist; resets to 0 on unlock.
    unlocked_now: bool       # True iff this summon crossed SHARDS_PER_UNLOCK.
    pity_activated: bool     # True iff hard pity rewrote the roll.
    jackpot: bool            # True iff natural roll landed in the 100-200 band.


def summon_cost(rarity: str) -> int:
    """Look up the gem cost for a rarity, falling back to rare's price."""
    return SUMMON_COST.get(rarity.lower(), SUMMON_COST["rare"])


def roll_shards(rng: random.Random | None = None) -> tuple[int, bool]:
    """Roll a single summon's shard count from SHARD_BANDS. Returns (count, jackpot)."""
    rng = rng or random
    bands = SHARD_BANDS
    weights = [b[2] for b in bands]
    chosen = rng.choices(bands, weights=weights, k=1)[0]
    lo, hi, _ = chosen
    count = rng.randint(lo, hi)
    return count, lo >= SHARDS_PER_UNLOCK


def roll_summon(
    current_shards: int,
    pity: int,
    rng: random.Random | None = None,
) -> SummonResult:
    """Resolve one summon on a specific hero.

    `current_shards` and `pity` are this player's state for THIS hero
    immediately before the summon. Caller persists the returned
    `new_total` and `new_pity` along with the gem deduction.
    """
    rng = rng or random
    if current_shards < 0 or pity < 0:
        raise ValueError("current_shards and pity must be non-negative")

    rolled_natural, jackpot = roll_shards(rng)
    rolled = rolled_natural
    pity_activated = False

    # Pity: this is the (pity + 1)-th summon since last unlock. If we
    # reach PITY_LIMIT without an unlock, top up to a guaranteed unlock
    # — but only if the natural roll wasn't already enough.
    pity_after = pity + 1
    new_total = current_shards + rolled
    if pity_after >= PITY_LIMIT and new_total < SHARDS_PER_UNLOCK:
        rolled = max(rolled, SHARDS_PER_UNLOCK - current_shards)
        new_total = current_shards + rolled
        pity_activated = True

    unlocked_now = current_shards < SHARDS_PER_UNLOCK <= new_total
    new_pity = 0 if unlocked_now else pity_after

    return SummonResult(
        shards_rolled=rolled,
        shards_rolled_natural=rolled_natural,
        new_total=new_total,
        new_pity=new_pity,
        unlocked_now=unlocked_now,
        pity_activated=pity_activated,
        jackpot=jackpot,
    )


def expected_shards_per_summon() -> float:
    """Analytical mean of the SHARD_BANDS distribution. Sanity check only."""
    total_w = sum(b[2] for b in SHARD_BANDS)
    return sum(((lo + hi) / 2) * (w / total_w) for lo, hi, w in SHARD_BANDS)
