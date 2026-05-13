"""/gather — start timed gathers and claim resources via an ephemeral panel.

Single slash command. The response is an ephemeral embed with five buttons
(Gold / Food / Wood / Claim Ready / Refresh) that edits itself in place.
Slots have no identity — capacity is just the cap on concurrent rows in
`marches`. Re-running `/gather` issues a fresh panel; ephemeral interaction
tokens last ~15 minutes, after which buttons stop working and the player
calls `/gather` again. We don't register persistent views — there is no
shared channel surface to restore on restart.
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.gather import (
    GATHER_DURATION_SECONDS,
    MAX_SLOTS,
    Resource,
    format_remaining,
    is_finished,
    remaining_seconds,
    roll_gather,
)
from wagame.ui import Flash, apply_flash

RESOURCE_EMOJI: dict[str, str] = {"gold": "💰", "food": "🍞", "wood": "🌲"}


class GatherCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="gather", description="Open your gathering panel.")
    async def gather(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        embed = await _render_embed(self.db, interaction.user)
        view = GatherView(self.db, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# -- DB helpers -------------------------------------------------------------


async def _fetch_player(db: Database, user_id: int):
    async with db.conn.execute(
        "SELECT * FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_marches(db: Database, user_id: int):
    async with db.conn.execute(
        "SELECT * FROM marches WHERE discord_user_id = ? ORDER BY finishes_at ASC",
        (user_id,),
    ) as cur:
        return await cur.fetchall()


async def _start_gather(db: Database, user_id: int, resource: Resource) -> Flash:
    player = await _fetch_player(db, user_id)
    capacity = int(player["march_capacity"])
    marches = await _fetch_marches(db, user_id)
    if len(marches) >= capacity:
        return Flash.err(
            f"All {capacity} march slot(s) busy — claim a finished gather first."
        )

    roll = roll_gather(resource)
    yield_pct = int(player["gather_yield_pct"])
    speed_pct = int(player["gather_speed_pct"])
    # Apply research bonuses at start time so the row is fully determined.
    boosted_base = int(roll.base * (100 + yield_pct) / 100)
    duration = max(1, int(GATHER_DURATION_SECONDS * (100 - min(99, speed_pct)) / 100))
    now = int(time.time())
    finishes_at = now + duration
    await db.conn.execute(
        """
        INSERT INTO marches (discord_user_id, resource, started_at, finishes_at,
                             yield_amount, crit)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, resource, now, finishes_at, boosted_base, 1 if roll.crit else 0),
    )
    await db.conn.commit()
    return Flash.ok(f"Sent a march to gather {resource}.")


async def _claim_ready(db: Database, user_id: int) -> tuple[int, dict[Resource, int], int]:
    """Claim every finished march. Returns (count, totals_by_resource, crits)."""
    now = int(time.time())
    async with db.conn.execute(
        "SELECT * FROM marches WHERE discord_user_id = ? AND finishes_at <= ?",
        (user_id, now),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        return 0, {}, 0

    totals: dict[Resource, int] = {"gold": 0, "food": 0, "wood": 0}
    crits = 0
    ids: list[int] = []
    for row in rows:
        resource: Resource = row["resource"]
        crit = bool(row["crit"])
        amount = int(row["yield_amount"]) * (2 if crit else 1)
        totals[resource] += amount
        if crit:
            crits += 1
        ids.append(int(row["id"]))

    await db.conn.execute(
        """
        UPDATE players SET
            gold = gold + ?,
            food = food + ?,
            wood = wood + ?
        WHERE discord_user_id = ?
        """,
        (totals["gold"], totals["food"], totals["wood"], user_id),
    )
    placeholders = ",".join("?" * len(ids))
    await db.conn.execute(f"DELETE FROM marches WHERE id IN ({placeholders})", ids)
    await db.conn.commit()
    return len(rows), totals, crits


# -- rendering --------------------------------------------------------------


async def _render_embed(db: Database, user: discord.abc.User) -> discord.Embed:
    player = await _fetch_player(db, user.id)
    marches = await _fetch_marches(db, user.id)
    capacity = int(player["march_capacity"])
    now = int(time.time())

    embed = discord.Embed(title="⛏️ Gathering")
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="💰 Gold", value=f"{player['gold']:,}", inline=True)
    embed.add_field(name="🍞 Food", value=f"{player['food']:,}", inline=True)
    embed.add_field(name="🌲 Wood", value=f"{player['wood']:,}", inline=True)

    in_use = len(marches)
    embed.add_field(
        name="Marches",
        value=f"{in_use}/{capacity} in use (cap {MAX_SLOTS} via research)",
        inline=False,
    )

    if marches:
        lines: list[str] = []
        for i, row in enumerate(marches, start=1):
            emoji = RESOURCE_EMOJI.get(row["resource"], "•")
            if is_finished(int(row["finishes_at"]), now):
                lines.append(f"`{i}.` {emoji} {row['resource']} — **ready to claim**")
            else:
                left = format_remaining(remaining_seconds(int(row["finishes_at"]), now))
                lines.append(f"`{i}.` {emoji} {row['resource']} — {left}")
        embed.add_field(name="In progress", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="In progress",
            value="No active gathers. Pick a resource to start one.",
            inline=False,
        )

    embed.set_footer(text=f"Each gather takes {GATHER_DURATION_SECONDS // 60} minutes.")
    return embed


# -- view -------------------------------------------------------------------


class GatherView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        # 15 minutes matches the ephemeral interaction-token lifetime; after
        # that, edit_message would fail anyway, so we time the buttons out.
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Ephemeral messages are only visible to the invoker, but defensively
        # reject anyone else just in case Discord ever changes that.
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your gathering panel.", ephemeral=True
            )
            return False
        return True

    async def _refresh(
        self, interaction: discord.Interaction, flash: Flash | None = None
    ) -> None:
        embed = await _render_embed(self.db, interaction.user)
        apply_flash(embed, flash)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _start(self, interaction: discord.Interaction, resource: Resource) -> None:
        result = await _start_gather(self.db, interaction.user.id, resource)
        # Success is implicit (the panel shows the new march); only red-flag failures.
        await self._refresh(
            interaction, flash=result if result.outcome.value == "error" else None
        )

    @discord.ui.button(label="Gold", emoji="💰", style=discord.ButtonStyle.primary, row=0)
    async def start_gold(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._start(interaction, "gold")

    @discord.ui.button(label="Food", emoji="🍞", style=discord.ButtonStyle.primary, row=0)
    async def start_food(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._start(interaction, "food")

    @discord.ui.button(label="Wood", emoji="🌲", style=discord.ButtonStyle.primary, row=0)
    async def start_wood(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._start(interaction, "wood")

    @discord.ui.button(label="Claim Ready", emoji="📦", style=discord.ButtonStyle.success, row=1)
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        count, totals, crits = await _claim_ready(self.db, interaction.user.id)
        if count == 0:
            await self._refresh(interaction, flash=Flash.info("Nothing finished yet."))
            return
        parts = [f"{v:,} {k}" for k, v in totals.items() if v]
        msg = f"Claimed {count} march(es): " + ", ".join(parts)
        if crits:
            msg += f" — {crits} crit{'s' if crits > 1 else ''}! ✨"
        await self._refresh(interaction, flash=Flash.ok(msg))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._refresh(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GatherCog(bot))
