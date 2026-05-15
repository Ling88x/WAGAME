"""/inventory — quick view of hero shards banked across the roster.

Single ephemeral embed. Lists every hero you have shards for, sorted by
shard count, plus owned counts so the player has a one-shot answer to
"where did my shards go?". No buttons, no view — keep it light.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from wagame.cogs.heroes import RARITY_EMOJI
from wagame.db import Database
from wagame.game.summon import SHARDS_PER_UNLOCK
from wagame.ui import NEUTRAL_COLOR


def _bar(value: int, total: int, width: int = 12) -> str:
    filled = min(width, max(0, round((value / total) * width)))
    return "█" * filled + "░" * (width - filled)


async def render_inventory_embed(
    db: Database, user: discord.abc.User
) -> discord.Embed:
    await db.get_or_create_player(user.id)

    async with db.conn.execute(
        """
        SELECT h.name, h.rarity, s.count,
               EXISTS(SELECT 1 FROM owned_heroes o
                      WHERE o.discord_user_id = ? AND o.hero_id = h.id) AS owned
        FROM hero_shards s
        JOIN heroes h ON h.id = s.hero_id
        WHERE s.discord_user_id = ? AND s.count > 0
        ORDER BY s.count DESC, h.name
        """,
        (user.id, user.id),
    ) as cur:
        rows = await cur.fetchall()

    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM owned_heroes WHERE discord_user_id = ?",
        (user.id,),
    ) as cur:
        owned_count = int((await cur.fetchone())["n"] or 0)
    async with db.conn.execute("SELECT COUNT(*) AS n FROM heroes") as cur:
        catalog_count = int((await cur.fetchone())["n"] or 0)

    embed = discord.Embed(
        title=f"🎒 {user.display_name}'s Inventory",
        color=NEUTRAL_COLOR,
        description=(
            f"**Heroes owned:** {owned_count}/{catalog_count}\n"
            f"**Heroes with banked shards:** {len(rows)}"
        ),
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    if not rows:
        embed.add_field(
            name="No shards banked",
            value=(
                "Shards drop from `/hunt` kills (small chance per kill), "
                "`/sighting` kills, `/vault` mythic rolls, `/council` "
                "rewards, and every `/summon` pull."
            ),
            inline=False,
        )
        return embed

    lines: list[str] = []
    for r in rows[:20]:
        emoji = RARITY_EMOJI.get(r["rarity"], "•")
        owned_marker = "✅" if int(r["owned"]) else "🔒"
        count = int(r["count"])
        capped = min(count, SHARDS_PER_UNLOCK)
        if int(r["owned"]):
            tail = f"{count:,} banked"
        else:
            tail = (
                f"`{_bar(capped, SHARDS_PER_UNLOCK)}` "
                f"{capped}/{SHARDS_PER_UNLOCK}"
            )
        lines.append(f"{owned_marker} {emoji} **{r['name']}** — {tail}")
    embed.add_field(
        name="Shards",
        value="\n".join(lines),
        inline=False,
    )
    embed.set_footer(
        text=(
            "✅ unlocked — extra shards bank for future level-up systems. "
            "🔒 locked — 100 shards unlocks the hero."
        )
    )
    return embed


class InventoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="inventory",
        description="See your hero shards and roster summary.",
    )
    async def inventory(self, interaction: discord.Interaction) -> None:
        embed = await render_inventory_embed(self.db, interaction.user)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InventoryCog(bot))
