"""`/wa` — central hub for every player-facing system.

One slash command opens a single ephemeral panel. The hub embed carries
short status lines (gathering, research, training, hunt, daily quota,
heroes) and a button per subsystem; clicking a button swaps the embed +
view in place. Each subsystem panel is rendered through its own cog's
existing helpers — the hub only injects an extra "🏠 Hub" button so the
player can navigate back without typing another slash command.

Decoupling: subsystem cogs don't know the hub exists. The Back button
is added externally via `view.add_item(BackToHubButton(...))` after
construction. Lazy imports inside button handlers sidestep the obvious
circular-import trap (hub imports cogs).

Old slash commands (`/gather`, `/hunt`, `/train`, `/research`,
`/heroes`, `/summon`, `/profile`) stay registered as power-user
shortcuts; the hub is the discovery surface.
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.daily import current_reset_day
from wagame.game.hunt import (
    DAILY_QUOTA_KILLS,
    ENERGY_CAP,
    get_tenebral,
    regen_energy,
)
from wagame.ui import NEUTRAL_COLOR

# -- status query ---------------------------------------------------------


async def _hub_state(db: Database, user_id: int) -> dict:
    """One pass over every relevant table — keeps `/wa` snappy."""
    player = await db.get_or_create_player(user_id)
    now = int(time.time())
    energy, _ = regen_energy(
        int(player["energy"]), int(player["energy_updated_at"]), now
    )

    async with db.conn.execute(
        "SELECT COUNT(*) AS n, MIN(finishes_at) AS earliest, "
        "SUM(CASE WHEN finishes_at <= ? THEN 1 ELSE 0 END) AS ready "
        "FROM marches WHERE discord_user_id = ?",
        (now, user_id),
    ) as cur:
        gather_row = await cur.fetchone()

    async with db.conn.execute(
        "SELECT * FROM research_jobs WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        research_job = await cur.fetchone()

    async with db.conn.execute(
        "SELECT j.*, t.name AS troop_name FROM training_jobs j "
        "JOIN troops t ON t.codename = j.troop_codename "
        "WHERE j.discord_user_id = ?",
        (user_id,),
    ) as cur:
        train_job = await cur.fetchone()

    async with db.conn.execute(
        "SELECT * FROM hunt_marches WHERE discord_user_id = ? AND resolved = 0",
        (user_id,),
    ) as cur:
        hunt_march = await cur.fetchone()

    async with db.conn.execute(
        "SELECT kills, reward_claimed FROM hunt_daily_progress "
        "WHERE discord_user_id = ? AND reset_day = ?",
        (user_id, current_reset_day().isoformat()),
    ) as cur:
        daily = await cur.fetchone()

    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM owned_heroes WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        heroes_row = await cur.fetchone()

    return {
        "player": player,
        "energy": energy,
        "gather_count": int(gather_row["n"] or 0),
        "gather_earliest": gather_row["earliest"],
        "gather_ready": int(gather_row["ready"] or 0),
        "research_job": research_job,
        "train_job": train_job,
        "hunt_march": hunt_march,
        "daily_kills": int(daily["kills"]) if daily else 0,
        "daily_claimed": bool(daily["reward_claimed"]) if daily else False,
        "heroes_count": int(heroes_row["n"] or 0),
    }


# -- status lines ---------------------------------------------------------


def _gather_line(state: dict) -> str:
    if state["gather_count"] == 0:
        return "Idle"
    if state["gather_ready"] > 0:
        return (
            f"{state['gather_count']} march(es) · "
            f"**{state['gather_ready']} ready to claim**"
        )
    earliest = state["gather_earliest"]
    return f"{state['gather_count']} march(es) · claim <t:{earliest}:R>"


def _research_line(state: dict) -> str:
    job = state["research_job"]
    if job is None:
        return "Idle"
    from wagame.research_data import get_node
    node = get_node(job["node_codename"])
    name = node.name if node else job["node_codename"]
    finishes_at = int(job["finishes_at"])
    now = int(time.time())
    when = "**done!**" if now >= finishes_at else f"ready <t:{finishes_at}:R>"
    return f"{name} → Lv {int(job['target_level'])} · {when}"


def _train_line(state: dict) -> str:
    job = state["train_job"]
    if job is None:
        return "Idle"
    finishes_at = int(job["finishes_at"])
    now = int(time.time())
    when = "**done!**" if now >= finishes_at else f"ready <t:{finishes_at}:R>"
    return f"{int(job['count']):,}x {job['troop_name']} · {when}"


def _hunt_line(state: dict) -> str:
    march = state["hunt_march"]
    if march is None:
        return "Idle"
    spec = get_tenebral(int(march["level"]))
    if march["engaged_at"] is None:
        started = int(march["started_at"])
        completes = int(march["completes_at"])
        midpoint = started + (completes - started) // 2
        return (
            f"🏇 Marching to Lv{spec.level} {spec.name} · engages <t:{midpoint}:R>"
        )
    completes = int(march["completes_at"])
    return f"🛡️ Returning from Lv{spec.level} {spec.name} · home <t:{completes}:R>"


def _quota_line(state: dict) -> str:
    kills = state["daily_kills"]
    if state["daily_claimed"]:
        return f"{kills}/{DAILY_QUOTA_KILLS} ✓ claimed"
    if kills >= DAILY_QUOTA_KILLS:
        return f"{kills}/{DAILY_QUOTA_KILLS} **ready to claim**"
    return f"{kills}/{DAILY_QUOTA_KILLS} kills"


# -- rendering ------------------------------------------------------------


async def render_hub_embed(db: Database, user: discord.abc.User) -> discord.Embed:
    state = await _hub_state(db, user.id)
    player = state["player"]

    embed = discord.Embed(
        title=f"🧙 {user.display_name}'s Hub",
        color=NEUTRAL_COLOR,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.description = (
        f"💰 {int(player['gold']):,}  ·  🍞 {int(player['food']):,}  ·  "
        f"🌲 {int(player['wood']):,}  ·  💎 {int(player['gems']):,}\n"
        f"⚡ Energy {state['energy']}/{ENERGY_CAP}"
    )

    embed.add_field(name="🦌 Hunt", value=_hunt_line(state), inline=False)
    embed.add_field(name="⛏️ Gathering", value=_gather_line(state), inline=False)
    embed.add_field(name="🔬 Research", value=_research_line(state), inline=False)
    embed.add_field(name="🏰 Training", value=_train_line(state), inline=False)
    embed.add_field(name="📅 Daily quota", value=_quota_line(state), inline=True)
    embed.add_field(
        name="🎴 Heroes",
        value=f"{state['heroes_count']} owned",
        inline=True,
    )
    return embed


async def _render_summon_picker_embed(
    db: Database, user: discord.abc.User
) -> discord.Embed:
    """Top-of-shards list. Full per-hero picker UI is a follow-up PR."""
    async with db.conn.execute(
        """
        SELECT h.name, h.rarity, COALESCE(s.count, 0) AS shards
        FROM heroes h
        LEFT JOIN hero_shards s
          ON s.discord_user_id = ? AND s.hero_id = h.id
        ORDER BY shards DESC, h.name
        LIMIT 15
        """,
        (user.id,),
    ) as cur:
        rows = await cur.fetchall()

    embed = discord.Embed(
        title="✨ Summon",
        color=NEUTRAL_COLOR,
        description=(
            "Use `/summon hero:<name>` to spend 💎 gems on a specific hero. "
            "Every summon yields ≥1 shard; 100 shards unlocks. Pity "
            "guarantees an unlock by the 100th summon on a single hero."
        ),
    )
    lines = [
        f"`{int(r['shards']):>3}/100` · {r['name']} ({r['rarity']})"
        for r in rows
        if int(r["shards"]) > 0
    ]
    if lines:
        embed.add_field(
            name="Heroes with banked shards",
            value="\n".join(lines[:10]),
            inline=False,
        )
    return embed


# -- views ----------------------------------------------------------------


class BackToHubButton(discord.ui.Button):
    """Injected onto every subview launched from the hub."""

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(
            label="Hub", emoji="🏠", style=discord.ButtonStyle.secondary, row=4
        )
        self.db = db
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = await render_hub_embed(self.db, interaction.user)
        view = HubView(self.db, self.owner_id)
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])


class _BackOnlyView(discord.ui.View):
    """Static-content subpanels (profile, summon picker) need only Back."""

    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.add_item(BackToHubButton(db, owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your panel.", ephemeral=True
            )
            return False
        return True


class HubView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your hub.", ephemeral=True
            )
            return False
        return True

    def _back(self) -> BackToHubButton:
        return BackToHubButton(self.db, self.owner_id)

    @discord.ui.button(label="Hunt", emoji="🦌", style=discord.ButtonStyle.danger, row=0)
    async def open_hunt(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.hunt import HuntView, _render_embed
        view = HuntView(self.db, self.owner_id)
        await view.initialize()
        view.add_item(self._back())
        embed = await _render_embed(
            self.db, interaction.user, view.selected_level, view.selected_hero_id
        )
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])

    @discord.ui.button(label="Gather", emoji="⛏️", style=discord.ButtonStyle.primary, row=0)
    async def open_gather(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.gather import GatherView, _render_embed
        view = GatherView(self.db, self.owner_id)
        view.add_item(self._back())
        embed = await _render_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])

    @discord.ui.button(label="Research", emoji="🔬", style=discord.ButtonStyle.primary, row=0)
    async def open_research(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.research import ResearchView, _render_embed
        view = ResearchView(self.db, self.owner_id)
        view.add_item(self._back())
        embed = await _render_embed(
            self.db, interaction.user, view.selected_codename
        )
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])

    @discord.ui.button(label="Train", emoji="🏰", style=discord.ButtonStyle.primary, row=0)
    async def open_train(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.train import (
            TrainView,
            _fetch_player,
            _fetch_unlocked_troops,
            _render_embed,
            claim_finished_training,
        )
        await claim_finished_training(self.db, self.owner_id)
        player = await _fetch_player(self.db, self.owner_id)
        troops = await _fetch_unlocked_troops(self.db, int(player["unlocked_tier"]))
        view = TrainView(self.db, self.owner_id, troops)
        view.add_item(self._back())
        embed = await _render_embed(
            self.db, interaction.user, view.selected_codename
        )
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])

    @discord.ui.button(label="Heroes", emoji="🎴", style=discord.ButtonStyle.secondary, row=1)
    async def open_heroes(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.heroes import HeroesView, _build_page_embeds, _fetch_owned
        rows = await _fetch_owned(self.db, self.owner_id)
        view = HeroesView(self.db, self.owner_id, total=len(rows))
        view.add_item(self._back())
        embeds = _build_page_embeds(interaction.user, rows, page=0)
        await interaction.response.edit_message(embeds=embeds, view=view)

    @discord.ui.button(label="Summon", emoji="✨", style=discord.ButtonStyle.secondary, row=1)
    async def open_summon(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = await _render_summon_picker_embed(self.db, interaction.user)
        view = _BackOnlyView(self.db, self.owner_id)
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])

    @discord.ui.button(label="Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def open_profile(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.profile import _profile_embed
        player = await self.db.get_or_create_player(self.owner_id)
        embed = _profile_embed(interaction.user, player)
        view = _BackOnlyView(self.db, self.owner_id)
        await interaction.response.edit_message(embed=embed, view=view, embeds=[])

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = await render_hub_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self, embeds=[])


# -- cog ------------------------------------------------------------------


class HubCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="wa", description="Open your Witch Arcana hub.")
    async def wa(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        view = HubView(self.db, interaction.user.id)
        embed = await render_hub_embed(self.db, interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HubCog(bot))
