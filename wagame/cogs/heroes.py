"""/heroes — list the heroes you own.

Until gacha lands, owned heroes only appear here when an owner uses
`/admin grant-hero`. Display is ephemeral.
"""

from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database

RARITY_COLOR = {
    "common": discord.Color.light_grey(),
    "rare": discord.Color.blue(),
    "epic": discord.Color.purple(),
    "legendary": discord.Color.gold(),
    "mythic": discord.Color.red(),
}


class HeroesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="heroes", description="List the heroes you own.")
    async def heroes(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        async with self.db.conn.execute(
            """
            SELECT h.codename, h.name, h.rarity, h.house, h.terrain,
                   o.level, o.dupes_pending
            FROM owned_heroes o
            JOIN heroes h ON h.id = o.hero_id
            WHERE o.discord_user_id = ?
            ORDER BY
                CASE h.rarity
                    WHEN 'mythic'    THEN 0
                    WHEN 'legendary' THEN 1
                    WHEN 'epic'      THEN 2
                    WHEN 'rare'      THEN 3
                    WHEN 'common'    THEN 4
                    ELSE 5
                END,
                h.name
            """,
            (interaction.user.id,),
        ) as cur:
            rows = await cur.fetchall()

        if not rows:
            await interaction.response.send_message(
                "You don't own any heroes yet. Once gacha is live, summon one — "
                "for now an admin can grant you one with `/admin grant-hero`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Heroes",
            color=discord.Color.purple(),
            description=f"You own **{len(rows)}** hero(es).",
        )
        for row in rows[:25]:
            traits = " · ".join(
                t for t in (row["rarity"], row["house"], row["terrain"]) if t
            )
            extra = f" (+{row['dupes_pending']} dupes)" if row["dupes_pending"] else ""
            embed.add_field(
                name=f"{row['name']}  · Lv {row['level']}{extra}",
                value=traits or "—",
                inline=False,
            )
        if len(rows) > 25:
            embed.set_footer(text=f"Showing 25 of {len(rows)}.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="hero", description="Show full info about a hero from the catalog.")
    @app_commands.describe(codename="Hero codename (e.g. ghostpink, waterlava).")
    async def hero(self, interaction: discord.Interaction, codename: str) -> None:
        codename = codename.strip().lower()
        async with self.db.conn.execute(
            "SELECT * FROM heroes WHERE codename = ?", (codename,)
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            await interaction.response.send_message(
                f"No hero with codename `{codename}` in the catalog.",
                ephemeral=True,
            )
            return

        bonuses = json.loads(row["bonuses_json"])
        tags = json.loads(row["tags_json"])
        color = RARITY_COLOR.get(row["rarity"], discord.Color.purple())

        embed = discord.Embed(title=row["name"], color=color)
        embed.add_field(name="Codename", value=f"`{row['codename']}`", inline=True)
        embed.add_field(name="Rarity", value=row["rarity"].capitalize(), inline=True)
        if row["release_date"]:
            embed.add_field(name="Released", value=row["release_date"], inline=True)
        if row["element"]:
            embed.add_field(name="Element", value=row["element"], inline=True)
        if row["house"]:
            embed.add_field(name="House", value=row["house"], inline=True)
        if row["terrain"]:
            embed.add_field(name="Terrain", value=row["terrain"], inline=True)
        if bonuses:
            embed.add_field(
                name="Bonuses",
                value="\n".join(f"• {b}" for b in bonuses),
                inline=False,
            )
        if tags:
            embed.set_footer(text=" · ".join(tags))
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HeroesCog(bot))
