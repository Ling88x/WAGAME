"""Hero summoning math — pull cost per rarity, shard roll curve, pity logic.

Pure functions, no DB. The cog layer (wagame/cogs/gacha.py) wraps this in
the panel + slash command. Testable in isolation via a seeded `Random`.

Curve shape (tuned to "between steep and middle"): mean is ~20 shards per
pull, so unlocks land around 5 pulls of natural luck. 1% chance per pull
to jackpot directly into 100+ shards. Pity guarantees an unlock no later
than the 100th pull on the same hero.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Costs are keyed by rarity. The user pinned legendary/epic/rare; the others
# fall back on the same slope (uncommon a notch cheaper than rare; mythic
# meaningfully steeper than legendary).
PULL_COST: dict[str, int] = {
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

# Hard pity: if a player makes this many pulls on one hero without
# unlocking, the next pull is topped up to push them past SHARDS_PER_UNLOCK.
# Combined with the minimum-1-shard floor, every hero unlocks in ≤100 pulls.
PITY_PULL_LIMIT = 100

# Daily gem bonus, auto-claimed on the first interaction after the WA reset
# boundary (21:00 UTC). The actual boundary lives in wagame/game/daily.py.
DAILY_GEM_BONUS = 100

# Onboarding grant. Mirrored in migration 007 for existing accounts; new
# accounts get this via the column DEFAULT.
STARTING_GEMS = 5000


# (low, high, weight). Sum of weights doesn't have to be 1 — we normalize.
# Means: 5, 19.5, 54.5, 89.5, 150. Weighted mean ≈ 19.8 shards/pull.
SHARD_BANDS: tuple[tuple[int, int, float], ...] = (
    (1,   9,    0.55),
    (10,  29,   0.28),
    (30,  79,   0.13),
    (80,  99,   0.03),
    (100, 200,  0.01),
)


@dataclass(frozen=True)
class PullResult:
    """Outcome of a single pull on one hero, ready for the cog to display."""

    shards_rolled: int       # Shards actually credited (after pity top-up).
    shards_rolled_natural: int  # What the dice said, before pity intervened.
    new_total: int           # Player's cumulative shard count after this pull.
    new_pity: int            # pity_pulls value to persist; resets to 0 on unlock.
    unlocked_now: bool       # True iff this pull crossed SHARDS_PER_UNLOCK.
    pity_activated: bool     # True iff hard pity rewrote the roll.
    jackpot: bool            # True iff natural roll landed in the 100-200 band.


def pull_cost(rarity: str) -> int:
    """Look up the gem cost for a rarity, falling back to rare's price."""
    return PULL_COST.get(rarity.lower(), PULL_COST["rare"])


def roll_shards(rng: random.Random | None = None) -> tuple[int, bool]:
    """Roll a single pull's shard count from SHARD_BANDS. Returns (count, jackpot)."""
    rng = rng or random
    bands = SHARD_BANDS
    weights = [b[2] for b in bands]
    chosen = rng.choices(bands, weights=weights, k=1)[0]
    lo, hi, _ = chosen
    count = rng.randint(lo, hi)
    return count, lo >= SHARDS_PER_UNLOCK


def roll_pull(
    current_shards: int,
    pity_pulls: int,
    rng: random.Random | None = None,
) -> PullResult:
    """Resolve one pull on a specific hero.

    `current_shards` and `pity_pulls` are this player's state for THIS hero
    immediately before the pull. Caller persists the returned `new_total`
    and `new_pity` along with the gem deduction.
    """
    rng = rng or random
    if current_shards < 0 or pity_pulls < 0:
        raise ValueError("current_shards and pity_pulls must be non-negative")

    rolled_natural, jackpot = roll_shards(rng)
    rolled = rolled_natural
    pity_activated = False

    # Pity: this pull is the (pity_pulls + 1)-th since last unlock. If we
    # reach PITY_PULL_LIMIT pulls without an unlock, top up to a guaranteed
    # unlock — but only if the natural roll wasn't already enough.
    pulls_after = pity_pulls + 1
    new_total = current_shards + rolled
    if pulls_after >= PITY_PULL_LIMIT and new_total < SHARDS_PER_UNLOCK:
        rolled = max(rolled, SHARDS_PER_UNLOCK - current_shards)
        new_total = current_shards + rolled
        pity_activated = True

    unlocked_now = current_shards < SHARDS_PER_UNLOCK <= new_total
    new_pity = 0 if unlocked_now else pulls_after

    return PullResult(
        shards_rolled=rolled,
        shards_rolled_natural=rolled_natural,
        new_total=new_total,
        new_pity=new_pity,
        unlocked_now=unlocked_now,
        pity_activated=pity_activated,
        jackpot=jackpot,
    )


def expected_shards_per_pull() -> float:
    """Analytical mean of the SHARD_BANDS distribution. Sanity check only."""
    total_w = sum(b[2] for b in SHARD_BANDS)
    return sum(((lo + hi) / 2) * (w / total_w) for lo, hi, w in SHARD_BANDS)
