"""/summon — summon hero shards for a specific hero.

One ephemeral panel per `/summon hero:<name>` invocation. The panel
shows the player's gem balance, progress toward unlock for the target
hero, pity counter, and a Summon button. Clicking Summon spends gems,
rolls shards, persists the result, and edits the embed in place with
a color-coded flash (green on unlock, blue on jackpot, neutral otherwise).

Heroes are auto-inserted into `owned_heroes` the moment their shard
count crosses 100 — no separate "claim" step. Excess shards keep
accumulating in `hero_shards` for later level-up systems.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from wagame.cogs.heroes import RARITY_COLOR, RARITY_EMOJI, _autocomplete_hero
from wagame.db import Database
from wagame.game.summon import (
    PITY_LIMIT,
    SHARDS_PER_UNLOCK,
    SummonResult,
    roll_summon,
    summon_cost,
)
from wagame.ui import Flash, apply_flash

log = logging.getLogger(__name__)


# -- DB helpers -------------------------------------------------------------


async def _fetch_hero_by_name(db: Database, needle: str):
    """Resolve a name OR codename into a hero row (case-insensitive)."""
    n = needle.strip().lower()
    async with db.conn.execute(
        "SELECT * FROM heroes WHERE LOWER(name) = ?", (n,)
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        return row
    async with db.conn.execute(
        "SELECT * FROM heroes WHERE codename = ?", (n,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_shards(db: Database, user_id: int, hero_id: int) -> tuple[int, int]:
    """Return (count, pity) for this user+hero; (0, 0) if no row yet."""
    async with db.conn.execute(
        "SELECT count, pity_pulls FROM hero_shards "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return 0, 0
    return int(row["count"]), int(row["pity_pulls"])


async def _is_owned(db: Database, user_id: int, hero_id: int) -> bool:
    async with db.conn.execute(
        "SELECT 1 FROM owned_heroes WHERE discord_user_id = ? AND hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        return await cur.fetchone() is not None


async def execute_summon(
    db: Database, user_id: int, hero_row
) -> tuple[SummonResult, Flash]:
    """Commit one summon on `hero_row` for `user_id`. Caller pre-checked gems."""
    cost = summon_cost(hero_row["rarity"])
    current, pity = await _fetch_shards(db, user_id, hero_row["id"])
    result = roll_summon(current, pity)

    await db.conn.execute(
        "UPDATE players SET gems = gems - ? WHERE discord_user_id = ?",
        (cost, user_id),
    )
    await db.conn.execute(
        """
        INSERT INTO hero_shards (discord_user_id, hero_id, count, pity_pulls)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
            count      = excluded.count,
            pity_pulls = excluded.pity_pulls
        """,
        (user_id, hero_row["id"], result.new_total, result.new_pity),
    )
    if result.unlocked_now:
        # First-time unlock — insert at level 1 with no dupes pending. If
        # the player already owns this hero (shouldn't happen since pity
        # resets on unlock, but defensive), we just bump dupes_pending.
        await db.conn.execute(
            """
            INSERT INTO owned_heroes (discord_user_id, hero_id, level)
            VALUES (?, ?, 1)
            ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                dupes_pending = dupes_pending + 1
            """,
            (user_id, hero_row["id"]),
        )
    await db.conn.commit()

    flash = _flash_for_result(result, hero_row["name"])
    return result, flash


def _flash_for_result(result: SummonResult, hero_name: str) -> Flash:
    if result.unlocked_now and result.pity_activated:
        return Flash.ok(
            f"🎁 Pity grants {result.shards_rolled} shards — {hero_name} unlocked!"
        )
    if result.unlocked_now:
        return Flash.ok(
            f"🎉 {hero_name} unlocked! ({result.shards_rolled} shards this summon)"
        )
    if result.jackpot:
        return Flash.info(
            f"🌟 Jackpot! {result.shards_rolled} shards "
            f"({result.new_total}/{SHARDS_PER_UNLOCK})"
        )
    return Flash.info(
        f"+{result.shards_rolled} shards ({result.new_total}/{SHARDS_PER_UNLOCK})"
    )


# -- rendering --------------------------------------------------------------


async def _render_embed(
    db: Database,
    user: discord.abc.User,
    hero_row,
    flash: Flash | None = None,
) -> discord.Embed:
    player = await db.get_or_create_player(user.id)
    count, pity = await _fetch_shards(db, user.id, hero_row["id"])
    owned = await _is_owned(db, user.id, hero_row["id"])

    color = RARITY_COLOR.get(hero_row["rarity"])
    rarity_emoji = RARITY_EMOJI.get(hero_row["rarity"], "•")
    embed = discord.Embed(
        title=f"🔮 Summon — {rarity_emoji} {hero_row['name']}",
        color=color,
    )
    if hero_row["image_url"]:
        embed.set_thumbnail(url=hero_row["image_url"])
    apply_flash(embed, flash)

    embed.add_field(name="💎 Gems", value=f"{int(player['gems']):,}", inline=True)
    embed.add_field(
        name="Summon cost",
        value=f"{summon_cost(hero_row['rarity']):,}",
        inline=True,
    )
    embed.add_field(
        name="Rarity",
        value=hero_row["rarity"].capitalize(),
        inline=True,
    )

    if owned:
        progress = f"**Unlocked** · {count} shards banked"
    else:
        bar = _progress_bar(min(count, SHARDS_PER_UNLOCK), SHARDS_PER_UNLOCK)
        progress = f"{bar}  {count}/{SHARDS_PER_UNLOCK}"
    embed.add_field(name="Unlock progress", value=progress, inline=False)

    embed.add_field(
        name="Pity",
        value=f"{pity}/{PITY_LIMIT} summons",
        inline=True,
    )
    embed.set_footer(
        text="Every summon yields ≥1 shard. Pity guarantees unlock by the 100th summon."
    )
    return embed


def _progress_bar(value: int, total: int, width: int = 20) -> str:
    filled = min(width, max(0, round((value / total) * width)))
    return "█" * filled + "░" * (width - filled)


# -- view -------------------------------------------------------------------


class SummonView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int, hero_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.hero_id = hero_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your summoning panel.", ephemeral=True
            )
            return False
        return True

    async def _hero_row(self):
        async with self.db.conn.execute(
            "SELECT * FROM heroes WHERE id = ?", (self.hero_id,)
        ) as cur:
            return await cur.fetchone()

    @discord.ui.button(label="Summon", emoji="🔮", style=discord.ButtonStyle.success)
    async def summon(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        hero_row = await self._hero_row()
        if hero_row is None:
            # Hero deleted between panel open and click — show error and bail.
            await interaction.response.edit_message(
                content="That hero no longer exists.", embed=None, view=None
            )
            return

        cost = summon_cost(hero_row["rarity"])
        player = await self.db.get_or_create_player(interaction.user.id)
        if int(player["gems"]) < cost:
            embed = await _render_embed(
                self.db,
                interaction.user,
                hero_row,
                flash=Flash.err(
                    f"Not enough gems — need {cost:,} 💎, "
                    f"have {int(player['gems']):,}."
                ),
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return

        _, flash = await execute_summon(self.db, interaction.user.id, hero_row)
        embed = await _render_embed(self.db, interaction.user, hero_row, flash=flash)
        await interaction.response.edit_message(embed=embed, view=self)


# -- cog --------------------------------------------------------------------


class SummonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="summon",
        description="Open a summoning chest for a specific hero.",
    )
    @app_commands.describe(
        hero="Search by name or codename — pick from suggestions.",
    )
    async def summon(self, interaction: discord.Interaction, hero: str) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        hero_row = await _fetch_hero_by_name(self.db, hero)
        if hero_row is None:
            from wagame.ui import Outcome, toast

            await interaction.response.send_message(
                embed=toast(
                    f"No hero matches `{hero}`. Try the autocomplete suggestions.",
                    Outcome.ERROR,
                ),
                ephemeral=True,
            )
            return

        view = SummonView(self.db, interaction.user.id, int(hero_row["id"]))
        embed = await _render_embed(self.db, interaction.user, hero_row)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @summon.autocomplete("hero")
    async def summon_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _autocomplete_hero(self.db, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SummonCog(bot))
