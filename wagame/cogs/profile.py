"""/profile command — shows the player's resources, level and march capacity."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.ui import NEUTRAL_COLOR


class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="profile", description="Show your Witch Arcana profile.")
    async def profile(self, interaction: discord.Interaction) -> None:
        player = await self.db.get_or_create_player(interaction.user.id)
        embed = _profile_embed(interaction.user, player)
        await interaction.response.send_message(embed=embed, ephemeral=True)


def _profile_embed(user: discord.abc.User, player) -> discord.Embed:
    embed = discord.Embed(
        title=f"🧙 {user.display_name}'s Profile",
        color=NEUTRAL_COLOR,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="Level", value=f"{player['player_level']}", inline=True)
    embed.add_field(name="Marches", value=f"{player['march_capacity']}", inline=True)
    embed.add_field(name="​", value="​", inline=True)
    embed.add_field(name="Gold", value=f"{player['gold']:,}", inline=True)
    embed.add_field(name="Food", value=f"{player['food']:,}", inline=True)
    embed.add_field(name="Wood", value=f"{player['wood']:,}", inline=True)
    embed.set_footer(text=f"Joined {player['created_at']} UTC")
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot))
