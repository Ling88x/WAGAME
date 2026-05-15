"""Shared progression helpers.

`grant_player_xp` is the single chokepoint for every "the player did
something" event: hunt, gather, sighting, daily, arena, council. It
applies XP, hands out level-up rewards (gems + a random shard), and
returns a `Flash`-ready summary so callers can surface the XP tick
visibly.

`drop_kill_shard` rolls a small chance of hero shards on a tenebral
kill — gives the regular hunt grind some shard income on top of the
explicit summon path.
"""

from __future__ import annotations

import logging
import random

from wagame.db import Database
from wagame.game.player_xp import (
    PLAYER_LEVELUP_GEMS,
    PLAYER_LEVELUP_SHARDS,
    apply_player_xp,
)

log = logging.getLogger(__name__)


# Hunt-kill shard drop tuning.
KILL_SHARD_CHANCE_BASE = 0.20    # 20% at lv1
KILL_SHARD_CHANCE_PER_LV = 0.04  # +4% per level (lv12 -> 68%)
KILL_SHARD_MAX_CHANCE = 0.80
KILL_SHARD_BASE = 1              # always drop ≥1 if rolled
KILL_SHARD_PER_LV = 0.25         # +0.25 max per level (rounded down)


# -- XP grant -------------------------------------------------------


async def _pick_random_unowned_hero(
    db: Database, user_id: int, rng: random.Random
) -> int | None:
    """Shard targets prefer a hero with banked progress, fall back to unowned."""
    async with db.conn.execute(
        "SELECT hero_id FROM hero_shards WHERE discord_user_id = ? AND count > 0",
        (user_id,),
    ) as cur:
        in_progress = [int(r["hero_id"]) for r in await cur.fetchall()]
    if in_progress:
        return rng.choice(in_progress)
    async with db.conn.execute(
        "SELECT id FROM heroes WHERE id NOT IN "
        "(SELECT hero_id FROM owned_heroes WHERE discord_user_id = ?)",
        (user_id,),
    ) as cur:
        unowned = [int(r["id"]) for r in await cur.fetchall()]
    if unowned:
        return rng.choice(unowned)
    async with db.conn.execute("SELECT id FROM heroes") as cur:
        anyhero = [int(r["id"]) for r in await cur.fetchall()]
    return rng.choice(anyhero) if anyhero else None


async def _credit_shards(
    db: Database, user_id: int, hero_id: int, count: int,
) -> None:
    await db.conn.execute(
        """
        INSERT INTO hero_shards (discord_user_id, hero_id, count)
        VALUES (?, ?, ?)
        ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
            count = count + excluded.count
        """,
        (user_id, hero_id, count),
    )


async def grant_player_xp(
    db: Database,
    user_id: int,
    amount: int,
    *,
    rng: random.Random | None = None,
) -> tuple[int, int, int]:
    """Apply `amount` player XP and pay level-up rewards.

    Returns `(new_level, levels_gained, shards_dropped_from_levelup)`.
    Always returns even on no-op (amount=0), with levels_gained=0.
    """
    if amount <= 0:
        async with db.conn.execute(
            "SELECT player_level FROM players WHERE discord_user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return (int(row["player_level"]) if row else 1), 0, 0

    r = rng or random
    async with db.conn.execute(
        "SELECT player_level, player_xp FROM players WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return 1, 0, 0
    result = apply_player_xp(int(row["player_level"]), int(row["player_xp"]), amount)
    shards_dropped = 0
    if result.levels_gained > 0:
        gem_reward = PLAYER_LEVELUP_GEMS * result.levels_gained
        # One shard target per level-up, in case multiple landed in one tick.
        for _ in range(result.levels_gained):
            hero_id = await _pick_random_unowned_hero(db, user_id, r)
            if hero_id is not None:
                await _credit_shards(db, user_id, hero_id, PLAYER_LEVELUP_SHARDS)
                shards_dropped += PLAYER_LEVELUP_SHARDS
        await db.conn.execute(
            "UPDATE players SET player_level = ?, player_xp = ?, "
            "gems = gems + ? WHERE discord_user_id = ?",
            (result.new_level, result.new_xp, gem_reward, user_id),
        )
    else:
        await db.conn.execute(
            "UPDATE players SET player_level = ?, player_xp = ? "
            "WHERE discord_user_id = ?",
            (result.new_level, result.new_xp, user_id),
        )
    await db.conn.commit()
    return result.new_level, result.levels_gained, shards_dropped


# -- hunt kill shard drop -------------------------------------------


def kill_shard_drop_count(mob_level: int, rng: random.Random) -> int:
    """Return how many shards a tenebral kill drops. 0 if the roll missed."""
    if mob_level < 1:
        return 0
    chance = min(
        KILL_SHARD_MAX_CHANCE,
        KILL_SHARD_CHANCE_BASE + KILL_SHARD_CHANCE_PER_LV * (mob_level - 1),
    )
    if rng.random() >= chance:
        return 0
    max_drop = max(KILL_SHARD_BASE, int(KILL_SHARD_BASE + mob_level * KILL_SHARD_PER_LV))
    return rng.randint(KILL_SHARD_BASE, max_drop)


async def drop_kill_shards(
    db: Database,
    user_id: int,
    mob_level: int,
    *,
    rng: random.Random | None = None,
) -> tuple[int, int | None]:
    """Roll + credit shards for a tenebral kill. Returns `(count, hero_id)`."""
    r = rng or random
    count = kill_shard_drop_count(mob_level, r)
    if count <= 0:
        return 0, None
    hero_id = await _pick_random_unowned_hero(db, user_id, r)
    if hero_id is None:
        return 0, None
    await _credit_shards(db, user_id, hero_id, count)
    await db.conn.commit()
    return count, hero_id
