"""Witch's Vault — daily open + streak loot box.

One Open per reset day. The button disables itself for the rest of the
day once clicked. Streak counter persists on `players.vault_streak`;
skipping a reset day resets it to 1 on the next open. Tier weights and
a flat RSS/gem multiplier step up at streak 7, 14, 30. Milestone days
pay extra gems + random shards on top of the regular bag.
"""

from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.daily import current_reset_day
from wagame.game.vault import (
    MILESTONE_DAYS,
    RewardBag,
    compute_next_streak,
    next_reset_unix,
    roll_reward,
    streak_multiplier,
)
from wagame.ui import NEUTRAL_COLOR, Flash, apply_flash

TIER_EMOJI: dict[str, str] = {
    "common":    "⚪",
    "uncommon":  "🟢",
    "rare":      "🔵",
    "legendary": "🟡",
    "mythic":    "🔴",
}


# -- open transaction --------------------------------------------------


async def _pick_shard_hero(
    db: Database, user_id: int, rng: random.Random
) -> int | None:
    """Mythic shards prefer a hero with banked progress, fall back to unowned."""
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
        candidates = [int(r["id"]) for r in await cur.fetchall()]
    return rng.choice(candidates) if candidates else None


async def open_vault(
    db: Database,
    user_id: int,
    *,
    rng: random.Random | None = None,
    today_iso: str | None = None,
) -> tuple[Flash, RewardBag | None]:
    """Commit one vault open. Returns `(flash, bag or None)`.

    `bag` is None if the player already opened today.
    """
    r = rng or random
    today_iso = today_iso or current_reset_day().isoformat()

    async with db.conn.execute(
        "SELECT vault_streak, last_vault_open_date FROM players "
        "WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    prev_streak = int(row["vault_streak"]) if row else 0
    prev_date = row["last_vault_open_date"] if row else None

    new_streak, already = compute_next_streak(prev_streak, prev_date, today_iso)
    if already:
        return (
            Flash.info("Vault already opened today. Come back after reset."),
            None,
        )

    bag = roll_reward(new_streak, rng=r)
    shard_hero_id = (
        await _pick_shard_hero(db, user_id, r) if bag.shard_count > 0 else None
    )

    # Credit RSS + gems (regular bag + milestone gems).
    total_gems = bag.gems + bag.milestone_gems
    await db.conn.execute(
        "UPDATE players SET gold = gold + ?, food = food + ?, "
        "wood = wood + ?, gems = gems + ?, "
        "vault_streak = ?, last_vault_open_date = ? "
        "WHERE discord_user_id = ?",
        (
            bag.gold, bag.food, bag.wood, total_gems,
            new_streak, today_iso, user_id,
        ),
    )

    # Bag shards (mythic-tier roll only).
    if bag.shard_count > 0 and shard_hero_id is not None:
        await db.conn.execute(
            """
            INSERT INTO hero_shards (discord_user_id, hero_id, count)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                count = count + excluded.count
            """,
            (user_id, shard_hero_id, bag.shard_count),
        )

    # Milestone shards roll their own random hero so it stays special.
    milestone_shard_hero_id = None
    if bag.milestone_shards > 0:
        milestone_shard_hero_id = await _pick_shard_hero(db, user_id, r)
        if milestone_shard_hero_id is not None:
            await db.conn.execute(
                """
                INSERT INTO hero_shards (discord_user_id, hero_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                    count = count + excluded.count
                """,
                (user_id, milestone_shard_hero_id, bag.milestone_shards),
            )
    await db.conn.commit()

    # Build flash.
    emoji = TIER_EMOJI.get(bag.tier, "🎁")
    bits = [
        f"{emoji} **{bag.tier.capitalize()}** bag —",
        f"+{bag.gold:,}/{bag.food:,}/{bag.wood:,} gold/food/wood",
    ]
    if total_gems > 0:
        bits.append(f"· +{total_gems:,} 💎")
    if bag.shard_count > 0:
        bits.append(f"· +{bag.shard_count} mythic shards")
    if bag.milestone_shards > 0:
        bits.append(
            f"· **Day {new_streak} milestone**: +{bag.milestone_shards} shards"
        )
    return Flash.ok(" ".join(bits)), bag


# -- rendering ---------------------------------------------------------


def _streak_bar(streak: int, width: int = 14) -> str:
    """Visual streak meter capped at 30 — every milestone is a marker."""
    cap = 30
    filled = min(width, int(width * min(streak, cap) / cap))
    return "🔥" * filled + "▫️" * (width - filled)


async def render_embed(db: Database, user: discord.abc.User) -> discord.Embed:
    async with db.conn.execute(
        "SELECT vault_streak, last_vault_open_date FROM players "
        "WHERE discord_user_id = ?",
        (user.id,),
    ) as cur:
        row = await cur.fetchone()
    streak = int(row["vault_streak"]) if row else 0
    last_date = row["last_vault_open_date"] if row else None
    today_iso = current_reset_day().isoformat()
    opened_today = last_date == today_iso

    # Effective streak displayed: if not opened today, show what the
    # streak WILL be after opening today (preview).
    display_streak = streak if opened_today else (
        streak + 1 if last_date == _previous_iso(today_iso) else 1
    )
    mul = streak_multiplier(display_streak)

    embed = discord.Embed(
        title="🗝 Witch's Vault",
        color=NEUTRAL_COLOR,
        description=(
            f"Open the vault once per reset day. Streak survives across "
            f"days as long as you don't skip. Reset at <t:{next_reset_unix()}:R>."
        ),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name=f"🔥 Streak Day {display_streak}",
        value=(
            f"`{_streak_bar(display_streak)}`\n"
            f"Reward multiplier: x{mul:.2f}"
        ),
        inline=False,
    )

    state = "✓ Opened today" if opened_today else "Ready to open"
    embed.add_field(name="Status", value=state, inline=True)

    next_milestones = sorted(
        d for d in MILESTONE_DAYS if d > display_streak
    )
    if next_milestones:
        nxt = next_milestones[0]
        bonus = MILESTONE_DAYS[nxt]
        embed.add_field(
            name=f"Next milestone: Day {nxt}",
            value=f"+{bonus[0]} 💎 · +{bonus[1]} random hero shards",
            inline=True,
        )
    else:
        embed.add_field(
            name="Milestones",
            value="All cleared — keep the streak alive for the multiplier.",
            inline=True,
        )

    embed.set_footer(
        text=(
            "Tier odds improve at day 7 / 14 / 30. Skip a day and the "
            "streak resets to 1."
        )
    )
    return embed


def _previous_iso(today_iso: str) -> str:
    import datetime as _dt
    return (_dt.date.fromisoformat(today_iso) - _dt.timedelta(days=1)).isoformat()


# -- view --------------------------------------------------------------


class VaultView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your vault.", ephemeral=True
            )
            return False
        return True

    async def _refresh(
        self, interaction: discord.Interaction, flash: Flash | None = None
    ) -> None:
        embed = await render_embed(self.db, interaction.user)
        apply_flash(embed, flash)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Open", emoji="🗝", style=discord.ButtonStyle.success, row=0)
    async def open_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        flash, _bag = await open_vault(self.db, interaction.user.id)
        await self._refresh(interaction, flash=flash)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self._refresh(interaction)


# -- cog ---------------------------------------------------------------


class VaultCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="vault",
        description="Open the Witch's Vault — daily loot with a streak.",
    )
    async def vault(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        embed = await render_embed(self.db, interaction.user)
        view = VaultView(self.db, interaction.user.id)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VaultCog(bot))
