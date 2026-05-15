"""Ghost Arena — async PvP, posts a 12h match in each configured channel.

Background loop scans `arena_configs` rows every ARENA_TICK_HOURS, picks
two enrolled players, builds Combatants from their rosters via the
existing `_build_combatant` helper in admin.py, runs `simulate_battle`
with a match-id seed (so the result can be replayed deterministically),
posts the fight log to the channel, and applies ELO + gem rewards.

Players opt in with `/arena enroll`. Owners point the bot at a channel
with `/admin set-arena-channel`. /arena stats and /arena leaderboard
expose the rating + record. `/admin trigger-arena` forces a tick for
the current guild — useful for testing without the 12h wait.
"""

from __future__ import annotations

import json
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from wagame.db import Database
from wagame.game.arena import (
    apply_elo,
    reward_gems,
)
from wagame.game.combat import simulate_battle
from wagame.ui import NEUTRAL_COLOR

log = logging.getLogger(__name__)

ARENA_TICK_HOURS = 12

# How recently a player must have been seen to be eligible for a draft.
ARENA_ACTIVITY_HORIZON_SECONDS = 30 * 24 * 3600


# -- candidate pool ----------------------------------------------------


async def _eligible_user_ids(db: Database) -> list[int]:
    """Players who opted in, own at least one hero, and are recently active."""
    cutoff_iso = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.gmtime(time.time() - ARENA_ACTIVITY_HORIZON_SECONDS),
    )
    async with db.conn.execute(
        """
        SELECT p.discord_user_id
        FROM players p
        WHERE p.arena_enrolled = 1
          AND p.last_seen >= ?
          AND EXISTS (
            SELECT 1 FROM owned_heroes o
            WHERE o.discord_user_id = p.discord_user_id
          )
        """,
        (cutoff_iso,),
    ) as cur:
        return [int(r["discord_user_id"]) for r in await cur.fetchall()]


async def _pick_pair(
    db: Database, rng: random.Random | None = None
) -> tuple[int, int] | None:
    r = rng or random
    ids = await _eligible_user_ids(db)
    if len(ids) < 2:
        return None
    a, b = r.sample(ids, 2)
    return a, b


# -- run a match -------------------------------------------------------


async def _record_outcome(
    db: Database,
    *,
    a_id: int,
    b_id: int,
    score_a: float,
) -> tuple[int, int, int, int]:
    """Apply ELO + gem rewards + win/loss counters. Returns new ratings + deltas."""
    async with db.conn.execute(
        "SELECT arena_rating FROM players WHERE discord_user_id = ?", (a_id,)
    ) as cur:
        a_rating = int((await cur.fetchone())["arena_rating"])
    async with db.conn.execute(
        "SELECT arena_rating FROM players WHERE discord_user_id = ?", (b_id,)
    ) as cur:
        b_rating = int((await cur.fetchone())["arena_rating"])

    change = apply_elo(a_rating, b_rating, score_a)
    gems_a, gems_b = reward_gems(score_a)

    a_wins_delta = 1 if score_a == 1.0 else 0
    a_losses_delta = 1 if score_a == 0.0 else 0
    a_draws_delta = 1 if score_a == 0.5 else 0

    await db.conn.execute(
        "UPDATE players SET arena_rating = ?, arena_wins = arena_wins + ?, "
        "arena_losses = arena_losses + ?, arena_draws = arena_draws + ?, "
        "gems = gems + ? "
        "WHERE discord_user_id = ?",
        (change.new_a, a_wins_delta, a_losses_delta, a_draws_delta, gems_a, a_id),
    )
    await db.conn.execute(
        "UPDATE players SET arena_rating = ?, arena_wins = arena_wins + ?, "
        "arena_losses = arena_losses + ?, arena_draws = arena_draws + ?, "
        "gems = gems + ? "
        "WHERE discord_user_id = ?",
        (
            change.new_b,
            1 - a_wins_delta - a_draws_delta if score_a == 0.0 else 0,
            1 if score_a == 1.0 else 0,
            a_draws_delta,
            gems_b,
            b_id,
        ),
    )
    await db.conn.commit()

    # Player XP — wins pay more than losses.
    from wagame.game.progression import grant_player_xp
    a_xp = 100 if score_a == 1.0 else (50 if score_a == 0.5 else 25)
    b_xp = 100 if score_a == 0.0 else (50 if score_a == 0.5 else 25)
    await grant_player_xp(db, a_id, a_xp)
    await grant_player_xp(db, b_id, b_xp)

    return change.new_a, change.new_b, change.delta_a, change.delta_b


async def run_arena_match(
    bot: commands.Bot,
    db: Database,
    *,
    guild_id: int,
    channel_id: int,
    rng: random.Random | None = None,
) -> int | None:
    """Pick a pair, simulate, post, and persist. Returns the match id or None
    when the pool was too small to make a match."""
    from wagame.cogs.admin import _build_combatant

    pair = await _pick_pair(db, rng=rng)
    if pair is None:
        log.info("arena: not enough eligible players for guild %d", guild_id)
        return None

    a_id, b_id = pair
    try:
        user_a = bot.get_user(a_id) or await bot.fetch_user(a_id)
        user_b = bot.get_user(b_id) or await bot.fetch_user(b_id)
    except discord.NotFound:
        log.warning("arena: unresolvable user in pair (%d, %d)", a_id, b_id)
        return None

    a_combatant = await _build_combatant(db, user_a)
    b_combatant = await _build_combatant(db, user_b)
    if a_combatant is None or b_combatant is None:
        log.info("arena: combatant build returned None, skipping match")
        return None

    # Reserve a match id first so we can seed the RNG with it.
    now = int(time.time())
    async with db.conn.execute(
        """
        INSERT INTO arena_matches
          (guild_id, a_user_id, b_user_id, rounds, log_json, fought_at)
        VALUES (?, ?, ?, 0, '[]', ?)
        """,
        (guild_id, a_id, b_id, now),
    ) as cur:
        match_id = int(cur.lastrowid)
    await db.conn.commit()

    result = simulate_battle(a_combatant, b_combatant, rng=random.Random(match_id))

    score_a = (
        1.0 if result.winner == a_combatant.name
        else 0.0 if result.winner == b_combatant.name
        else 0.5
    )
    new_a, new_b, delta_a, delta_b = await _record_outcome(
        db, a_id=a_id, b_id=b_id, score_a=score_a,
    )
    winner_id = (
        a_id if score_a == 1.0 else b_id if score_a == 0.0 else None
    )
    await db.conn.execute(
        "UPDATE arena_matches SET winner_user_id = ?, rounds = ?, log_json = ? "
        "WHERE id = ?",
        (winner_id, result.rounds, json.dumps(result.log), match_id),
    )
    await db.conn.execute(
        "UPDATE arena_configs SET last_match_at = ? WHERE guild_id = ?",
        (now, guild_id),
    )
    await db.conn.commit()

    # Post embed.
    embed = _match_embed(
        a_combatant.name, b_combatant.name, result,
        new_a, new_b, delta_a, delta_b,
    )
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden):
        log.warning("arena: cannot resolve channel %d", channel_id)
        return match_id
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("arena: post failed in channel %d: %s", channel_id, exc)
    return match_id


def _match_embed(
    a_name: str,
    b_name: str,
    result,
    a_rating: int,
    b_rating: int,
    a_delta: int,
    b_delta: int,
) -> discord.Embed:
    color = (
        discord.Color.from_rgb(63, 185, 80) if result.winner != "draw"
        else NEUTRAL_COLOR
    )
    joined = "\n".join(result.log)
    if len(joined) > 3800:
        joined = joined[:3800] + "\n… (trimmed)"
    embed = discord.Embed(
        title=f"⚔️ Ghost Arena · {a_name} vs {b_name}",
        description=joined,
        color=color,
    )
    embed.add_field(
        name=f"{a_name}",
        value=f"Rating **{a_rating}** ({a_delta:+d})",
        inline=True,
    )
    embed.add_field(
        name=f"{b_name}",
        value=f"Rating **{b_rating}** ({b_delta:+d})",
        inline=True,
    )
    embed.set_footer(
        text=(
            f"Winner: {result.winner} · {result.rounds} round(s) · "
            "Matches fire every 12h."
        )
    )
    return embed


# -- cog --------------------------------------------------------------


class ArenaCog(
    commands.GroupCog, group_name="arena", group_description="Async PvP arena."
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.arena_tick.start()

    async def cog_unload(self) -> None:
        self.arena_tick.cancel()

    @tasks.loop(hours=ARENA_TICK_HOURS)
    async def arena_tick(self) -> None:
        try:
            await self._tick_all_arenas()
        except Exception:
            log.exception("arena_tick failed")

    @arena_tick.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _tick_all_arenas(self) -> None:
        async with self.db.conn.execute(
            "SELECT guild_id, channel_id FROM arena_configs WHERE enabled = 1"
        ) as cur:
            arenas = await cur.fetchall()
        for row in arenas:
            await run_arena_match(
                self.bot,
                self.db,
                guild_id=int(row["guild_id"]),
                channel_id=int(row["channel_id"]),
            )

    @app_commands.command(
        name="enroll",
        description="Opt in or out of the Ghost Arena draft.",
    )
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    async def enroll(
        self,
        interaction: discord.Interaction,
        state: app_commands.Choice[str],
    ) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        enrolled = 1 if state.value == "on" else 0
        await self.db.conn.execute(
            "UPDATE players SET arena_enrolled = ? WHERE discord_user_id = ?",
            (enrolled, interaction.user.id),
        )
        await self.db.conn.commit()
        verb = "enrolled in" if enrolled else "withdrawn from"
        await interaction.response.send_message(
            f"You're {verb} the Ghost Arena. "
            "Matches fire every 12h in configured server channels.",
            ephemeral=True,
        )

    @app_commands.command(name="stats", description="Show your arena record.")
    async def stats(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        async with self.db.conn.execute(
            "SELECT arena_rating, arena_wins, arena_losses, arena_draws, "
            "arena_enrolled FROM players WHERE discord_user_id = ?",
            (interaction.user.id,),
        ) as cur:
            row = await cur.fetchone()
        rating = int(row["arena_rating"])
        wins = int(row["arena_wins"])
        losses = int(row["arena_losses"])
        draws = int(row["arena_draws"])
        enrolled = "yes" if int(row["arena_enrolled"]) else "no (use `/arena enroll`)"

        embed = discord.Embed(
            title=f"⚔️ {interaction.user.display_name}'s Arena Record",
            color=NEUTRAL_COLOR,
            description=(
                f"Rating **{rating}**\n"
                f"Record: **{wins}W / {losses}L / {draws}D**\n"
                f"Enrolled: {enrolled}"
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="leaderboard",
        description="Top 10 arena players by rating.",
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        async with self.db.conn.execute(
            "SELECT discord_user_id, arena_rating, arena_wins, arena_losses "
            "FROM players WHERE arena_enrolled = 1 OR arena_wins > 0 OR arena_losses > 0 "
            "ORDER BY arena_rating DESC LIMIT 10"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            await interaction.response.send_message(
                "No arena participants yet. Use `/arena enroll on` to join.",
                ephemeral=True,
            )
            return
        lines = []
        for i, r in enumerate(rows, start=1):
            try:
                user = (
                    self.bot.get_user(int(r["discord_user_id"]))
                    or await self.bot.fetch_user(int(r["discord_user_id"]))
                )
                name = user.display_name
            except (discord.NotFound, discord.HTTPException):
                name = f"<@{int(r['discord_user_id'])}>"
            lines.append(
                f"`{i:>2}.` **{name}** — {int(r['arena_rating'])} "
                f"({int(r['arena_wins'])}W / {int(r['arena_losses'])}L)"
            )
        embed = discord.Embed(
            title="🏆 Ghost Arena Leaderboard",
            description="\n".join(lines),
            color=NEUTRAL_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ArenaCog(bot))
