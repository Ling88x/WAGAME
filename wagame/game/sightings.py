"""Pure logic for Tenebral Sightings — no Discord, no DB.

A sighting is a one-shot rare mob spawn delivered to a player via DM.
The mob has the same HP as the corresponding regular tenebral level
(see game/hunt.py.TENEBRAL_TABLE) but the rewards scale up: RSS_MULTIPLIER
and XP_MULTIPLIER on the base hunt rewards, plus 1-5 random hero shards.

Window: gap between sightings per player is uniform random in
[MIN_GAP_SECONDS, MAX_GAP_SECONDS]. Once spawned the player has
EXPIRY_SECONDS to engage before the sighting vanishes (no rewards, no
retry).

The cog owns the background tasks.loop that scans eligible players and
spawns. This module only does math.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Gap between sightings per player — randomised at each spawn.
MIN_GAP_SECONDS = 6 * 3600
MAX_GAP_SECONDS = 24 * 3600

# Engagement window once the sighting fires.
EXPIRY_SECONDS = 2 * 3600

# Reward scaling vs a regular hunt of the same level.
RSS_MULTIPLIER = 3
XP_MULTIPLIER = 3

# Random hero shards per kill.
SHARD_DROP_MIN = 1
SHARD_DROP_MAX = 5

# Player is "active" if last_seen is within this window.
ACTIVITY_HORIZON_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True)
class SightingSpec:
    level: int
    hp: int
    bonus_rss: int   # total bonus RSS to split evenly across gold/food/wood
    bonus_xp: int    # hero XP awarded on kill
    shard_count: int


def pick_gap(rng: random.Random | None = None) -> int:
    """Random gap until the player's next sighting."""
    r = rng or random
    return r.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS)


def roll_level(hunt_level_unlocked: int, rng: random.Random | None = None) -> int:
    """Pick a sighting level within reach of the player.

    Window: [max(1, unlock - 2), min(12, unlock + 1)]. Allows the rare
    "stretch" sighting one level above current comfort, but keeps most
    spawns inside the player's grind band.
    """
    if hunt_level_unlocked < 1:
        raise ValueError("hunt_level_unlocked must be >= 1")
    r = rng or random
    lo = max(1, hunt_level_unlocked - 2)
    hi = min(12, hunt_level_unlocked + 1)
    return r.randint(lo, hi)


def build_spec(level: int, rng: random.Random | None = None) -> SightingSpec:
    """Compose a SightingSpec for `level`. Reads HP and rewards from hunt."""
    from wagame.game.hunt import get_tenebral
    tenebral = get_tenebral(level)
    r = rng or random
    return SightingSpec(
        level=level,
        hp=tenebral.hp,
        bonus_rss=tenebral.reward_rss * RSS_MULTIPLIER,
        bonus_xp=tenebral.reward_xp * XP_MULTIPLIER,
        shard_count=r.randint(SHARD_DROP_MIN, SHARD_DROP_MAX),
    )


def is_expired(expires_at: int, now: int) -> bool:
    return now >= expires_at


def is_active_player(last_seen_unix: int, now: int) -> bool:
    """Filter out long-inactive accounts so we don't spam DMs into the void."""
    return now - last_seen_unix <= ACTIVITY_HORIZON_SECONDS
