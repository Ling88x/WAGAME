"""Admin tools — restricted to the bot owner.

`/admin grant` — top up resources for any user (yourself by default).
`/admin reset` — wipe a player's row so the next interaction recreates them.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from wagame.cogs.heroes import _autocomplete_hero
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

    @app_commands.command(
        name="grant-hero",
        description="Grant a hero to a player (owner only).",
    )
    @app_commands.describe(
        hero="Search by name or codename — pick from suggestions.",
        user="Recipient. Defaults to you.",
        level="Starting level (default 1).",
    )
    @app_commands.check(_is_bot_owner)
    async def grant_hero(
        self,
        interaction: discord.Interaction,
        hero: str,
        user: discord.User | None = None,
        level: int = 1,
    ) -> None:
        target = user or interaction.user
        codename = hero.strip().lower()
        if level < 1:
            await interaction.response.send_message("Level must be >= 1.", ephemeral=True)
            return

        async with self.db.conn.execute(
            "SELECT id, name FROM heroes WHERE LOWER(name) = ?", (codename,)
        ) as cur:
            hero_row = await cur.fetchone()
        # Codename still accepted as a power-user fallback.
        if hero_row is None:
            async with self.db.conn.execute(
                "SELECT id, name FROM heroes WHERE codename = ?", (codename,)
            ) as cur:
                hero_row = await cur.fetchone()
        if hero_row is None:
            await interaction.response.send_message(
                f"No hero matches `{hero}`.", ephemeral=True
            )
            return

        await self.db.get_or_create_player(target.id)
        await self.db.conn.execute(
            """
            INSERT INTO owned_heroes (discord_user_id, hero_id, level)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                dupes_pending = dupes_pending + 1
            """,
            (target.id, hero_row["id"], level),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Granted **{hero_row['name']}** (Lv {level}) to {target.mention}.",
            ephemeral=True,
        )

    @grant_hero.autocomplete("hero")
    async def grant_hero_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _autocomplete_hero(self.db, current)

    @app_commands.command(
        name="unlock-tier",
        description="Set a player's max trainable troop tier (owner only).",
    )
    @app_commands.describe(
        tier="Highest tier the player can train (1-4).",
        user="Target player. Defaults to you.",
    )
    @app_commands.check(_is_bot_owner)
    async def unlock_tier(
        self,
        interaction: discord.Interaction,
        tier: int,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        if tier < 1 or tier > 4:
            await interaction.response.send_message(
                "Tier must be between 1 and 4.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target.id)
        await self.db.conn.execute(
            "UPDATE players SET unlocked_tier = ? WHERE discord_user_id = ?",
            (tier, target.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"{target.mention} can now train up to tier T{tier}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set-queue-cap",
        description="Set a player's summoning queue cap (owner only).",
    )
    @app_commands.describe(
        cap="New batch-size cap (>= 1).",
        user="Target player. Defaults to you.",
    )
    @app_commands.check(_is_bot_owner)
    async def set_queue_cap(
        self,
        interaction: discord.Interaction,
        cap: int,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        if cap < 1:
            await interaction.response.send_message("Cap must be >= 1.", ephemeral=True)
            return
        await self.db.get_or_create_player(target.id)
        await self.db.conn.execute(
            "UPDATE players SET training_queue_cap = ? WHERE discord_user_id = ?",
            (cap, target.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"{target.mention} queue cap set to {cap:,}.", ephemeral=True
        )

    @app_commands.command(
        name="set-train-speed",
        description="Set a player's training speed boost percent (owner only).",
    )
    @app_commands.describe(
        percent="Speed boost percent (0-99). Reduces training time.",
        user="Target player. Defaults to you.",
    )
    @app_commands.check(_is_bot_owner)
    async def set_train_speed(
        self,
        interaction: discord.Interaction,
        percent: int,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        if percent < 0 or percent > 99:
            await interaction.response.send_message(
                "Percent must be between 0 and 99.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target.id)
        await self.db.conn.execute(
            "UPDATE players SET training_speed_boost_pct = ? WHERE discord_user_id = ?",
            (percent, target.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"{target.mention} training speed boost set to {percent}%.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set-research",
        description="Force-set a player's research level for a node (owner only).",
    )
    @app_commands.describe(
        node="Research node codename (see wagame/research_data.py).",
        level="Target level (0 to clear, up to the node's max).",
        user="Target player. Defaults to you.",
    )
    @app_commands.check(_is_bot_owner)
    async def set_research(
        self,
        interaction: discord.Interaction,
        node: str,
        level: int,
        user: discord.User | None = None,
    ) -> None:
        from wagame.cogs.research import ALLOWED_EFFECT_COLUMNS
        from wagame.research_data import get_node

        node_spec = get_node(node.strip().lower())
        if node_spec is None:
            await interaction.response.send_message(
                f"No research node `{node}`.", ephemeral=True
            )
            return
        if level < 0 or level > node_spec.max_level:
            await interaction.response.send_message(
                f"Level must be in [0, {node_spec.max_level}].", ephemeral=True
            )
            return
        if node_spec.effect_column not in ALLOWED_EFFECT_COLUMNS:
            await interaction.response.send_message(
                "Node targets a non-whitelisted column. Refusing.", ephemeral=True
            )
            return

        target = user or interaction.user
        await self.db.get_or_create_player(target.id)
        async with self.db.conn.execute(
            "SELECT level FROM player_research WHERE discord_user_id = ? AND node_codename = ?",
            (target.id, node_spec.codename),
        ) as cur:
            row = await cur.fetchone()
        previous = int(row["level"]) if row else 0
        delta = (level - previous) * node_spec.effect_per_level

        await self.db.conn.execute(
            """
            INSERT INTO player_research (discord_user_id, node_codename, level)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_user_id, node_codename) DO UPDATE SET level = excluded.level
            """,
            (target.id, node_spec.codename, level),
        )
        await self.db.conn.execute(
            f"UPDATE players SET {node_spec.effect_column} = {node_spec.effect_column} + ? "
            "WHERE discord_user_id = ?",
            (delta, target.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"{target.mention}: {node_spec.name} -> Lv {level} "
            f"(delta on `{node_spec.effect_column}`: {delta:+d}).",
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
