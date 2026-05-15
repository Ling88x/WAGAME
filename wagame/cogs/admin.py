"""Admin tools — restricted to the bot owner.

Two surfaces:
  * Slash subcommands (`/admin grant`, `/admin grant-hero`, ...) — power-user
    shortcuts kept around for muscle-memory.
  * `/admin hub` — single-message panel of buttons, each opening a modal
    for the action's inputs. Same panel is reachable from `/wa` for any
    interaction.user that passes `is_owner` — see HubView.open_admin.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from wagame.cogs.heroes import _autocomplete_hero
from wagame.db import Database
from wagame.game.hero_levels import apply_xp_gain


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
        gems="Gems to add.",
    )
    @app_commands.check(_is_bot_owner)
    async def grant(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
        gold: int = 0,
        food: int = 0,
        wood: int = 0,
        gems: int = 0,
    ) -> None:
        target = user or interaction.user
        await self.db.get_or_create_player(target.id)
        await self.db.conn.execute(
            "UPDATE players SET gold = gold + ?, food = food + ?, wood = wood + ?, "
            "gems = gems + ? WHERE discord_user_id = ?",
            (gold, food, wood, gems, target.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Granted to {target.mention}: gold +{gold:,}, food +{food:,}, "
            f"wood +{wood:,}, gems +{gems:,}.",
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
        name="grant-hero-xp",
        description="Grant XP to a player's hero (owner only).",
    )
    @app_commands.describe(
        hero="Hero name (must be owned by the recipient).",
        amount="XP to add (>= 1).",
        user="Recipient. Defaults to you.",
    )
    @app_commands.check(_is_bot_owner)
    async def grant_hero_xp(
        self,
        interaction: discord.Interaction,
        hero: str,
        amount: int,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        if amount < 1:
            await interaction.response.send_message(
                "Amount must be >= 1.", ephemeral=True
            )
            return

        needle = hero.strip().lower()
        async with self.db.conn.execute(
            "SELECT id, name FROM heroes WHERE LOWER(name) = ?", (needle,)
        ) as cur:
            hero_row = await cur.fetchone()
        if hero_row is None:
            async with self.db.conn.execute(
                "SELECT id, name FROM heroes WHERE codename = ?", (needle,)
            ) as cur:
                hero_row = await cur.fetchone()
        if hero_row is None:
            await interaction.response.send_message(
                f"No hero matches `{hero}`.", ephemeral=True
            )
            return

        await self.db.get_or_create_player(target.id)
        async with self.db.conn.execute(
            "SELECT level, xp FROM owned_heroes "
            "WHERE discord_user_id = ? AND hero_id = ?",
            (target.id, hero_row["id"]),
        ) as cur:
            owned = await cur.fetchone()
        if owned is None:
            await interaction.response.send_message(
                f"{target.mention} doesn't own **{hero_row['name']}**. "
                "Grant the hero first with `/admin grant-hero`.",
                ephemeral=True,
            )
            return

        result = apply_xp_gain(owned["level"], owned["xp"], amount)
        await self.db.conn.execute(
            "UPDATE owned_heroes SET level = ?, xp = ? "
            "WHERE discord_user_id = ? AND hero_id = ?",
            (result.new_level, result.new_xp, target.id, hero_row["id"]),
        )
        await self.db.conn.commit()

        msg = (
            f"**{hero_row['name']}** +{amount:,} XP → "
            f"Lv {result.new_level} ({result.new_xp:,} XP)"
        )
        if result.levels_gained > 0:
            msg += f" · gained {result.levels_gained} level(s)"
        await interaction.response.send_message(msg, ephemeral=True)

    @grant_hero_xp.autocomplete("hero")
    async def grant_hero_xp_autocomplete(
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

    @app_commands.command(name="hub", description="Open the admin hub (owner only).")
    @app_commands.check(_is_bot_owner)
    async def hub(self, interaction: discord.Interaction) -> None:
        view = AdminHubView(self.db, interaction.user.id)
        embed = render_admin_hub_embed()
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )

    @app_commands.command(
        name="spawn-sighting",
        description="Force-spawn a Tenebral Sighting (owner only).",
    )
    @app_commands.describe(
        level="Tenebral level (1-12).",
        user="Target player. Defaults to you.",
    )
    @app_commands.check(_is_bot_owner)
    async def spawn_sighting(
        self,
        interaction: discord.Interaction,
        level: int,
        user: discord.User | None = None,
    ) -> None:
        from wagame.cogs.sightings import SightingsCog, spawn_sighting
        target = user or interaction.user
        if level < 1 or level > 12:
            await interaction.response.send_message(
                "Level must be 1-12.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target.id)
        sighting_id = await spawn_sighting(
            self.db, target.id, hunt_level_unlocked=level, forced_level=level
        )
        dm_sent = await SightingsCog.dispatch_dm(
            interaction.client, self.db, sighting_id  # type: ignore[arg-type]
        )
        dm_state = "DM delivered" if dm_sent else "DM not delivered (see logs)"
        await interaction.response.send_message(
            f"Spawned sighting #{sighting_id} (Lv{level}) for {target.mention}. "
            f"{dm_state}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="set-council-channel",
        description="Configure the Witch's Council channel for this server.",
    )
    @app_commands.describe(channel="Channel where quest announcements post.")
    @app_commands.check(_is_bot_owner)
    async def set_council_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Run this inside a server.", ephemeral=True
            )
            return
        await self.db.conn.execute(
            """
            INSERT INTO council_configs (guild_id, channel_id, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id, enabled = 1
            """,
            (interaction.guild_id, channel.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Council channel set to {channel.mention}. "
            "Quest spawn / payout announcements will post here.",
            ephemeral=True,
        )

    @app_commands.command(
        name="trigger-council",
        description="Force the Council loop to tick now (owner only).",
    )
    @app_commands.check(_is_bot_owner)
    async def trigger_council(self, interaction: discord.Interaction) -> None:
        from wagame.cogs.council import CouncilCog
        cog: CouncilCog | None = interaction.client.get_cog("CouncilCog")  # type: ignore[assignment]
        if cog is None:
            await interaction.response.send_message(
                "Council cog not loaded.", ephemeral=True
            )
            return
        await cog._tick()
        await interaction.response.send_message(
            "Council tick fired.", ephemeral=True
        )

    @app_commands.command(
        name="set-arena-channel",
        description="Configure the Ghost Arena channel for this server.",
    )
    @app_commands.describe(channel="Channel where 12h match recaps will post.")
    @app_commands.check(_is_bot_owner)
    async def set_arena_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Run this inside a server.", ephemeral=True
            )
            return
        await self.db.conn.execute(
            """
            INSERT INTO arena_configs (guild_id, channel_id, enabled)
            VALUES (?, ?, 1)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id, enabled = 1
            """,
            (interaction.guild_id, channel.id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Ghost Arena channel set to {channel.mention}. "
            "Matches will start posting on the next 12h tick.",
            ephemeral=True,
        )

    @app_commands.command(
        name="trigger-arena",
        description="Force the Ghost Arena loop to fire now (owner only).",
    )
    @app_commands.check(_is_bot_owner)
    async def trigger_arena(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Run this inside a server.", ephemeral=True
            )
            return
        async with self.db.conn.execute(
            "SELECT channel_id FROM arena_configs WHERE guild_id = ?",
            (interaction.guild_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await interaction.response.send_message(
                "No arena channel configured. Use `/admin set-arena-channel` first.",
                ephemeral=True,
            )
            return
        from wagame.cogs.arena import run_arena_match
        match_id = await run_arena_match(
            interaction.client,  # type: ignore[arg-type]
            self.db,
            guild_id=interaction.guild_id,
            channel_id=int(row["channel_id"]),
        )
        if match_id is None:
            await interaction.response.send_message(
                "Couldn't draft a pair — need ≥2 enrolled players with heroes.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            f"Match #{match_id} posted.", ephemeral=True
        )

    @app_commands.command(
        name="simulate-combat",
        description="Run the auto-battler between two players and post the log.",
    )
    @app_commands.describe(
        user_a="Attacker (defaults to you).",
        user_b="Defender.",
    )
    @app_commands.check(_is_bot_owner)
    async def simulate_combat(
        self,
        interaction: discord.Interaction,
        user_b: discord.User,
        user_a: discord.User | None = None,
    ) -> None:
        from wagame.game.combat import simulate_battle

        attacker = user_a or interaction.user
        await self.db.get_or_create_player(attacker.id)
        await self.db.get_or_create_player(user_b.id)
        a_combatant = await _build_combatant(self.db, attacker)
        b_combatant = await _build_combatant(self.db, user_b)
        if a_combatant is None or b_combatant is None:
            await interaction.response.send_message(
                "One of the players has no heroes — nothing to fight with.",
                ephemeral=True,
            )
            return

        result = simulate_battle(a_combatant, b_combatant)
        # Discord caps embed description at 4096 chars; we trim if needed.
        joined = "\n".join(result.log)
        if len(joined) > 3800:
            joined = joined[:3800] + "\n… (trimmed)"
        embed = discord.Embed(
            title=f"⚔️ {attacker.display_name} vs {user_b.display_name}",
            description=joined,
            color=discord.Color.from_rgb(248, 81, 73),
        )
        embed.set_footer(
            text=f"Winner: {result.winner} · {result.rounds} round(s)"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="test-dm",
        description="Send a hello-world DM to verify the bot can reach you.",
    )
    @app_commands.describe(user="Target user. Defaults to you.")
    @app_commands.check(_is_bot_owner)
    async def test_dm(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        try:
            await target.send(
                "🔔 Test DM from wagame — your inbox is reachable. "
                "If you see this, Hero Diary and Sighting DMs should land too."
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{target.mention} blocks the bot's DMs "
                "(server-side privacy setting). Diary + Sightings DMs will "
                "silently fail until that's changed.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"DM failed with HTTP error: {exc}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Test DM sent to {target.mention}.", ephemeral=True
        )


# -- admin hub: render + modals + view ------------------------------------


def render_admin_hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛠 Admin Hub",
        color=discord.Color.from_rgb(248, 81, 73),
        description=(
            "Owner-only testing tools. Each button opens a modal for inputs. "
            "Target user ID is optional — blank means you."
        ),
    )
    embed.add_field(
        name="Resources",
        value="💰 Grant RSS · 🎴 Grant Hero · 📈 Grant Hero XP",
        inline=False,
    )
    embed.add_field(
        name="Combat / Economy",
        value="⚒️ Unlock Tier · 📦 Queue Cap · ⏱ Train Speed · 🔬 Set Research",
        inline=False,
    )
    embed.add_field(
        name="Danger",
        value="🗑️ Reset Player wipes the target's entire row.",
        inline=False,
    )
    return embed


def _parse_int(value: str, default: int = 0) -> int:
    s = (value or "").strip()
    return int(s) if s else default


def _parse_target_id(value: str, fallback: int) -> int:
    s = (value or "").strip()
    if not s:
        return fallback
    # Tolerate <@123> mentions as well as bare IDs.
    s = s.lstrip("<@!").rstrip(">")
    return int(s)


async def _lookup_hero_id(db: Database, needle: str) -> tuple[int, str] | None:
    n = needle.strip().lower()
    async with db.conn.execute(
        "SELECT id, name FROM heroes WHERE LOWER(name) = ? OR codename = ?",
        (n, n),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    return int(row["id"]), row["name"]


class _AdminModal(discord.ui.Modal):
    """Shared base — wires the db handle and the default-target id."""

    def __init__(self, db: Database, owner_id: int, *, title: str) -> None:
        super().__init__(title=title)
        self.db = db
        self.owner_id = owner_id


class GrantRSSModal(_AdminModal):
    gold = discord.ui.TextInput(label="Gold", default="0", required=False)
    food = discord.ui.TextInput(label="Food", default="0", required=False)
    wood = discord.ui.TextInput(label="Wood", default="0", required=False)
    gems = discord.ui.TextInput(label="Gems", default="0", required=False)
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Grant RSS")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            gold = _parse_int(self.gold.value)
            food = _parse_int(self.food.value)
            wood = _parse_int(self.wood.value)
            gems = _parse_int(self.gems.value)
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message(
                "Invalid number in one of the fields.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target_id)
        await self.db.conn.execute(
            "UPDATE players SET gold = gold + ?, food = food + ?, "
            "wood = wood + ?, gems = gems + ? WHERE discord_user_id = ?",
            (gold, food, wood, gems, target_id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Granted to <@{target_id}>: +{gold:,} gold, +{food:,} food, "
            f"+{wood:,} wood, +{gems:,} gems.",
            ephemeral=True,
        )


class GrantHeroModal(_AdminModal):
    hero = discord.ui.TextInput(label="Hero name or codename")
    level = discord.ui.TextInput(label="Starting level", default="1", required=False)
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Grant Hero")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            level = max(1, _parse_int(self.level.value, 1))
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad input.", ephemeral=True)
            return
        hit = await _lookup_hero_id(self.db, self.hero.value)
        if hit is None:
            await interaction.response.send_message(
                f"No hero matches `{self.hero.value}`.", ephemeral=True
            )
            return
        hero_id, name = hit
        await self.db.get_or_create_player(target_id)
        await self.db.conn.execute(
            """
            INSERT INTO owned_heroes (discord_user_id, hero_id, level)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                dupes_pending = dupes_pending + 1
            """,
            (target_id, hero_id, level),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Granted **{name}** (Lv {level}) to <@{target_id}>.", ephemeral=True
        )


class GrantHeroXPModal(_AdminModal):
    hero = discord.ui.TextInput(label="Hero name or codename")
    amount = discord.ui.TextInput(label="XP to add")
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Grant Hero XP")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            amount = _parse_int(self.amount.value, 0)
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad input.", ephemeral=True)
            return
        if amount < 1:
            await interaction.response.send_message(
                "Amount must be >= 1.", ephemeral=True
            )
            return
        hit = await _lookup_hero_id(self.db, self.hero.value)
        if hit is None:
            await interaction.response.send_message(
                f"No hero matches `{self.hero.value}`.", ephemeral=True
            )
            return
        hero_id, name = hit
        await self.db.get_or_create_player(target_id)
        async with self.db.conn.execute(
            "SELECT level, xp FROM owned_heroes "
            "WHERE discord_user_id = ? AND hero_id = ?",
            (target_id, hero_id),
        ) as cur:
            owned = await cur.fetchone()
        if owned is None:
            await interaction.response.send_message(
                f"<@{target_id}> doesn't own {name}. Grant the hero first.",
                ephemeral=True,
            )
            return
        result = apply_xp_gain(int(owned["level"]), int(owned["xp"]), amount)
        await self.db.conn.execute(
            "UPDATE owned_heroes SET level = ?, xp = ? "
            "WHERE discord_user_id = ? AND hero_id = ?",
            (result.new_level, result.new_xp, target_id, hero_id),
        )
        await self.db.conn.commit()
        suffix = (
            f" · gained {result.levels_gained} level(s)"
            if result.levels_gained
            else ""
        )
        await interaction.response.send_message(
            f"**{name}** +{amount:,} XP → Lv {result.new_level} "
            f"({result.new_xp:,} XP){suffix}",
            ephemeral=True,
        )


class UnlockTierModal(_AdminModal):
    tier = discord.ui.TextInput(label="Tier (1-4)")
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Unlock Troop Tier")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            tier = _parse_int(self.tier.value, 1)
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad input.", ephemeral=True)
            return
        if tier < 1 or tier > 4:
            await interaction.response.send_message(
                "Tier must be 1-4.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target_id)
        await self.db.conn.execute(
            "UPDATE players SET unlocked_tier = ? WHERE discord_user_id = ?",
            (tier, target_id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"<@{target_id}> can now train up to T{tier}.", ephemeral=True
        )


class SetQueueCapModal(_AdminModal):
    cap = discord.ui.TextInput(label="New batch cap (>= 1)")
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Set Training Queue Cap")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            cap = _parse_int(self.cap.value, 1)
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad input.", ephemeral=True)
            return
        if cap < 1:
            await interaction.response.send_message(
                "Cap must be >= 1.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target_id)
        await self.db.conn.execute(
            "UPDATE players SET training_queue_cap = ? WHERE discord_user_id = ?",
            (cap, target_id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"<@{target_id}> queue cap set to {cap:,}.", ephemeral=True
        )


class SetTrainSpeedModal(_AdminModal):
    percent = discord.ui.TextInput(label="Speed boost percent (0-99)")
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Set Training Speed")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            pct = _parse_int(self.percent.value, 0)
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad input.", ephemeral=True)
            return
        if pct < 0 or pct > 99:
            await interaction.response.send_message(
                "Percent must be 0-99.", ephemeral=True
            )
            return
        await self.db.get_or_create_player(target_id)
        await self.db.conn.execute(
            "UPDATE players SET training_speed_boost_pct = ? "
            "WHERE discord_user_id = ?",
            (pct, target_id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"<@{target_id}> training speed set to {pct}%.", ephemeral=True
        )


class SetResearchModal(_AdminModal):
    node = discord.ui.TextInput(label="Node codename (see research_data.py)")
    level = discord.ui.TextInput(label="Target level (0 to clear)")
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Set Research Level")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from wagame.cogs.research import ALLOWED_EFFECT_COLUMNS
        from wagame.research_data import get_node

        try:
            level = _parse_int(self.level.value, 0)
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad input.", ephemeral=True)
            return
        node_spec = get_node(self.node.value.strip().lower())
        if node_spec is None:
            await interaction.response.send_message(
                f"No research node `{self.node.value}`.", ephemeral=True
            )
            return
        if level < 0 or level > node_spec.max_level:
            await interaction.response.send_message(
                f"Level must be 0..{node_spec.max_level}.", ephemeral=True
            )
            return
        if node_spec.effect_column not in ALLOWED_EFFECT_COLUMNS:
            await interaction.response.send_message(
                "Node targets a non-whitelisted column. Refusing.",
                ephemeral=True,
            )
            return

        await self.db.get_or_create_player(target_id)
        async with self.db.conn.execute(
            "SELECT level FROM player_research "
            "WHERE discord_user_id = ? AND node_codename = ?",
            (target_id, node_spec.codename),
        ) as cur:
            row = await cur.fetchone()
        previous = int(row["level"]) if row else 0
        delta = (level - previous) * node_spec.effect_per_level

        await self.db.conn.execute(
            "INSERT INTO player_research (discord_user_id, node_codename, level) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(discord_user_id, node_codename) DO UPDATE SET "
            "level = excluded.level",
            (target_id, node_spec.codename, level),
        )
        await self.db.conn.execute(
            f"UPDATE players SET {node_spec.effect_column} = "
            f"{node_spec.effect_column} + ? WHERE discord_user_id = ?",
            (delta, target_id),
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"<@{target_id}>: {node_spec.name} → Lv {level} "
            f"(`{node_spec.effect_column}` {delta:+d}).",
            ephemeral=True,
        )


class ResetPlayerModal(_AdminModal):
    confirm = discord.ui.TextInput(
        label="Type RESET to confirm",
        placeholder="RESET",
    )
    target = discord.ui.TextInput(
        label="Target user ID (blank = self)", required=False
    )

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(db, owner_id, title="Reset Player (destructive)")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self.confirm.value.strip() != "RESET":
            await interaction.response.send_message(
                "Confirmation phrase mismatched — nothing reset.", ephemeral=True
            )
            return
        try:
            target_id = _parse_target_id(self.target.value, self.owner_id)
        except ValueError:
            await interaction.response.send_message("Bad target id.", ephemeral=True)
            return
        await self.db.conn.execute(
            "DELETE FROM players WHERE discord_user_id = ?", (target_id,)
        )
        await self.db.conn.commit()
        await interaction.response.send_message(
            f"Reset <@{target_id}>. Their next interaction recreates them.",
            ephemeral=True,
        )


class AdminHubView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await interaction.client.is_owner(interaction.user):
            await interaction.response.send_message(
                "Owner only.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Grant RSS", emoji="💰", style=discord.ButtonStyle.primary, row=0)
    async def grant_rss(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            GrantRSSModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Grant Hero", emoji="🎴", style=discord.ButtonStyle.primary, row=0)
    async def grant_hero(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            GrantHeroModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Grant XP", emoji="📈", style=discord.ButtonStyle.primary, row=0)
    async def grant_xp(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            GrantHeroXPModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Unlock Tier", emoji="⚒️", style=discord.ButtonStyle.secondary, row=1)
    async def unlock_tier_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            UnlockTierModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Queue Cap", emoji="📦", style=discord.ButtonStyle.secondary, row=1)
    async def queue_cap_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            SetQueueCapModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Train Speed", emoji="⏱", style=discord.ButtonStyle.secondary, row=1)
    async def train_speed_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            SetTrainSpeedModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Set Research", emoji="🔬", style=discord.ButtonStyle.secondary, row=2)
    async def set_research_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            SetResearchModal(self.db, self.owner_id)
        )

    @discord.ui.button(label="Reset Player", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def reset_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(
            ResetPlayerModal(self.db, self.owner_id)
        )


async def _build_combatant(db: Database, user: discord.abc.User):
    """Compose a `wagame.game.combat.Combatant` from a player's DB rows.

    Picks the player's top-rarity highest-level hero as commander, sums
    every owned troop's atk and hp, and pulls research multipliers off
    the player row. Returns None if the player owns no heroes.
    """
    from wagame.game.combat import Combatant
    from wagame.game.hero_levels import atk_eff, command_pct

    async with db.conn.execute(
        """
        SELECT h.name, h.rarity, o.level FROM owned_heroes o
        JOIN heroes h ON h.id = o.hero_id
        WHERE o.discord_user_id = ?
        ORDER BY
            CASE h.rarity
                WHEN 'mythic'    THEN 0
                WHEN 'legendary' THEN 1
                WHEN 'epic'      THEN 2
                WHEN 'rare'      THEN 3
                WHEN 'uncommon'  THEN 4
                WHEN 'common'    THEN 5
                ELSE 6
            END,
            o.level DESC,
            h.name
        LIMIT 1
        """,
        (user.id,),
    ) as cur:
        hero_row = await cur.fetchone()
    if hero_row is None:
        return None

    async with db.conn.execute(
        """
        SELECT t.attack, t.hp, o.count FROM owned_troops o
        JOIN troops t ON t.codename = o.troop_codename
        WHERE o.discord_user_id = ? AND o.count > 0
        """,
        (user.id,),
    ) as cur:
        troop_rows = await cur.fetchall()
    troops_atk = sum(int(r["attack"]) * int(r["count"]) for r in troop_rows)
    troops_hp = sum(int(r["hp"]) * int(r["count"]) for r in troop_rows)

    async with db.conn.execute(
        "SELECT troop_attack_pct, troop_hp_pct FROM players "
        "WHERE discord_user_id = ?",
        (user.id,),
    ) as cur:
        player = await cur.fetchone()

    hero_level = int(hero_row["level"])
    return Combatant(
        name=user.display_name,
        hero_name=hero_row["name"],
        hero_atk=atk_eff(hero_row["rarity"], hero_level),
        hero_command_pct=command_pct(hero_level),
        troops_atk=troops_atk,
        troops_hp=troops_hp,
        troop_attack_pct=int(player["troop_attack_pct"]) if player else 0,
        troop_hp_pct=int(player["troop_hp_pct"]) if player else 0,
    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
