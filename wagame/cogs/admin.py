"""Admin tools — restricted to the bot owner.

`/admin grant` — top up resources for any user (yourself by default).
`/admin reset` — wipe a player's row so the next interaction recreates them.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database


async def _is_bot_owner(interaction: discord.Interaction) -> bool:
    return await interaction.client.is_owner(interaction.user)


class AdminCog(commands.GroupCog, group_name="admin", group_description="Admin tools."):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="grant", description="Grant resources to a player (owner only).")
    @app_commands.describe(
        user="Recipient. Defaults to you.",
        gold="Gold to add.",
        food="Food to add.",
        wood="Wood to add.",
    )
    @app_commands.check(_is_bot_owner)
    async def grant(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        gold: int = 0,
        food: int = 0,
        wood: int = 0,
    ) -> None:
        target = user or interaction.user
        await self.db.get_or_create_player(target.id)
        await self.db.conn.execute(
            "UPDATE players SET gold = gold + ?, food = food + ?, wood = wood + ? "
            "WHERE discord_user_id = ?",
            (gold, food, wood, target.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Granted to {target.mention}: gold +{gold:,}, food +{food:,}, wood +{wood:,}.",
            ephemeral=True,
        )

    @app_commands.command(name="reset", description="Wipe a player's account (owner only).")
    @app_commands.describe(user="Player to reset. Defaults to you.")
    @app_commands.check(_is_bot_owner)
    async def reset(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        await self.db.conn.execute(
            "DELETE FROM players WHERE discord_user_id = ?", (target.id,)
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Reset {target.mention}. Their next interaction will recreate the account.",
            ephemeral=True,
        )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = "This command is restricted to the bot owner."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
