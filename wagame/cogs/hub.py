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

    async with db.conn.execute(
        "SELECT * FROM tenebral_sightings "
        "WHERE discord_user_id = ? AND claimed = 0 "
        "ORDER BY spawned_at DESC LIMIT 1",
        (user_id,),
    ) as cur:
        sighting = await cur.fetchone()

    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM bestiary_entries "
        "WHERE discord_user_id = ? AND mob_kind = 'tenebral' "
        "AND encounter_count > 0",
        (user_id,),
    ) as cur:
        bestiary_row = await cur.fetchone()

    async with db.conn.execute(
        "SELECT id, kind, target, progress, ends_at FROM council_quests "
        "WHERE settled = 0 ORDER BY started_at DESC LIMIT 1"
    ) as cur:
        council_quest = await cur.fetchone()
    council_user_amount = 0
    if council_quest is not None:
        async with db.conn.execute(
            "SELECT amount FROM council_contributions "
            "WHERE quest_id = ? AND user_id = ?",
            (int(council_quest["id"]), user_id),
        ) as cur:
            cu = await cur.fetchone()
        council_user_amount = int(cu["amount"]) if cu else 0

    today_iso = current_reset_day().isoformat()
    vault_opened_today = (player["last_vault_open_date"] == today_iso)

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
        "sighting": sighting,
        "bestiary_documented": int(bestiary_row["n"] or 0),
        "vault_streak": int(player["vault_streak"] or 0),
        "vault_opened_today": vault_opened_today,
        "council_quest": council_quest,
        "council_user_amount": council_user_amount,
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


def _vault_line(state: dict) -> str:
    streak = state["vault_streak"]
    if state["vault_opened_today"]:
        return f"🔥 Streak {streak} · opened today"
    if streak == 0:
        return "**Ready to open** — start a streak"
    return f"🔥 Streak {streak} · **ready to open**"


def _council_line(state: dict) -> str | None:
    quest = state["council_quest"]
    if quest is None:
        return None
    from wagame.game.council import describe, unit_label
    kind = quest["kind"]
    target = int(quest["target"])
    progress = int(quest["progress"])
    pct = int(100 * progress / max(1, target))
    your = state["council_user_amount"]
    return (
        f"🏛 **{describe(kind)}** · {progress:,}/{target:,} "
        f"{unit_label(kind)} ({pct}%) · you: {your:,}\n"
        f"Ends <t:{int(quest['ends_at'])}:R>"
    )


async def _has_active_sighting(db: Database, user_id: int) -> bool:
    async with db.conn.execute(
        "SELECT 1 FROM tenebral_sightings "
        "WHERE discord_user_id = ? AND claimed = 0 LIMIT 1",
        (user_id,),
    ) as cur:
        return await cur.fetchone() is not None


def _sighting_line(state: dict) -> str | None:
    """Active Tenebral Sighting summary line, or None when no sighting."""
    row = state["sighting"]
    if row is None:
        return None
    spec = get_tenebral(int(row["level"]))
    return (
        f"🌙 **Lv{spec.level} {spec.name}** spotted! "
        f"Expires <t:{int(row['expires_at'])}:R>. "
        f"Reward: +{int(row['bonus_rss']):,} RSS · "
        f"+{int(row['bonus_shard_count'])}x shards"
    )


# -- rendering ------------------------------------------------------------


def _short_num(n: int) -> str:
    """Compact form: 12_345 → '12.3k', 1_234_567 → '1.23M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".rstrip("0").rstrip(".")
    if n >= 10_000:
        return f"{n / 1_000:.1f}k".rstrip("0").rstrip(".")
    return f"{n:,}"


def _energy_bar(current: int, cap: int, width: int = 14) -> str:
    fill = min(width, max(0, int(width * current / max(1, cap))))
    return "█" * fill + "░" * (width - fill)


def _action_lines(state: dict) -> list[str]:
    """Things demanding a click right now — claim, done, ready."""
    lines: list[str] = []

    if state["daily_kills"] >= DAILY_QUOTA_KILLS and not state["daily_claimed"]:
        lines.append("📅 **Daily quest** · ready to claim")

    job = state["research_job"]
    if job is not None and int(job["finishes_at"]) <= int(time.time()):
        from wagame.research_data import get_node
        node = get_node(job["node_codename"])
        name = node.name if node else job["node_codename"]
        lines.append(f"🔬 **{name} → Lv {int(job['target_level'])}** · done!")

    tj = state["train_job"]
    if tj is not None and int(tj["finishes_at"]) <= int(time.time()):
        lines.append(f"🏰 **{int(tj['count']):,}x {tj['troop_name']}** · ready")

    if state["gather_ready"] > 0:
        lines.append(
            f"⛏️ **{state['gather_ready']} gather(s)** · ready to claim"
        )

    if not state["vault_opened_today"]:
        lines.append("🗝 **Vault** · ready to open")

    return lines


def _progress_lines(state: dict) -> list[str]:
    """Things still ticking — hunt march, training, research, gather, council."""
    lines: list[str] = []
    now = int(time.time())

    march = state["hunt_march"]
    if march is not None:
        spec = get_tenebral(int(march["level"]))
        if march["engaged_at"] is None:
            started = int(march["started_at"])
            completes = int(march["completes_at"])
            mid = started + (completes - started) // 2
            lines.append(
                f"🦌 🏇 Marching to Lv{spec.level} {spec.name} · engages <t:{mid}:R>"
            )
        else:
            lines.append(
                f"🦌 🛡️ Returning Lv{spec.level} {spec.name} · home "
                f"<t:{int(march['completes_at'])}:R>"
            )

    if state["gather_count"] > 0 and state["gather_ready"] == 0:
        earliest = state["gather_earliest"]
        lines.append(
            f"⛏️ {state['gather_count']} march(es) · claim <t:{earliest}:R>"
        )

    job = state["research_job"]
    if job is not None and int(job["finishes_at"]) > now:
        from wagame.research_data import get_node
        node = get_node(job["node_codename"])
        name = node.name if node else job["node_codename"]
        lines.append(
            f"🔬 {name} → Lv {int(job['target_level'])} · ready "
            f"<t:{int(job['finishes_at'])}:R>"
        )

    tj = state["train_job"]
    if tj is not None and int(tj["finishes_at"]) > now:
        lines.append(
            f"🏰 {int(tj['count']):,}x {tj['troop_name']} · ready "
            f"<t:{int(tj['finishes_at'])}:R>"
        )

    quest = state["council_quest"]
    if quest is not None:
        from wagame.game.council import describe, unit_label
        kind = quest["kind"]
        target = int(quest["target"])
        progress = int(quest["progress"])
        pct = int(100 * progress / max(1, target))
        your = state["council_user_amount"]
        lines.append(
            f"🏛 {describe(kind)} · {progress:,}/{target:,} "
            f"{unit_label(kind)} ({pct}%) · you: {your:,} · "
            f"<t:{int(quest['ends_at'])}:R>"
        )

    return lines


async def render_hub_embed(db: Database, user: discord.abc.User) -> discord.Embed:
    state = await _hub_state(db, user.id)
    player = state["player"]

    from wagame.game.player_xp import title_for
    pl_level = int(player["player_level"])
    pl_title = title_for(pl_level)
    embed = discord.Embed(
        title=f"🔥 {user.display_name}'s Campus · {pl_title} Lv {pl_level}",
        color=NEUTRAL_COLOR,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.description = (
        f"💰 {_short_num(int(player['gold']))}  ·  "
        f"🍞 {_short_num(int(player['food']))}  ·  "
        f"🌲 {_short_num(int(player['wood']))}  ·  "
        f"💎 {_short_num(int(player['gems']))}\n"
        f"⚡ `{_energy_bar(state['energy'], ENERGY_CAP)}` "
        f"{state['energy']}/{ENERGY_CAP}"
    )

    # Sighting always surfaces (limited window, high priority).
    sighting_line = _sighting_line(state)
    if sighting_line:
        embed.add_field(name="🌙 Sighting", value=sighting_line, inline=False)

    actions = _action_lines(state)
    if actions:
        embed.add_field(
            name="⚠️ Action needed",
            value="\n".join(actions),
            inline=False,
        )

    progress = _progress_lines(state)
    if progress:
        embed.add_field(
            name="🕐 In progress",
            value="\n".join(progress),
            inline=False,
        )

    # Daily rhythm — only when streak is going (idle = hidden).
    if state["vault_streak"] > 0 and state["vault_opened_today"]:
        embed.add_field(
            name="🔁 Daily",
            value=f"🗝 Vault streak {state['vault_streak']} · opened today",
            inline=False,
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
        is_admin = await interaction.client.is_owner(interaction.user)
        embed = await render_hub_embed(self.db, interaction.user)
        has_sighting = await _has_active_sighting(self.db, self.owner_id)
        view = HubView(
            self.db, self.owner_id, is_admin=is_admin, has_sighting=has_sighting,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class HeroesTabButton(discord.ui.Button):
    """One of the three tabs (Roster / Summon / Shards) injected into
    every Heroes sub-view so the player can flip between them without
    going back to the hub first."""

    def __init__(
        self, db: Database, owner_id: int, target: str, current: str,
    ) -> None:
        labels = {"roster": ("🎴", "Roster"), "summon": ("✨", "Summon"),
                  "shards": ("🎒", "Shards")}
        emoji, label = labels[target]
        style = (
            discord.ButtonStyle.primary if target == current
            else discord.ButtonStyle.secondary
        )
        super().__init__(label=label, emoji=emoji, style=style, row=2,
                         disabled=(target == current))
        self.db = db
        self.owner_id = owner_id
        self.target = target

    async def callback(self, interaction: discord.Interaction) -> None:
        await _open_heroes_tab(interaction, self.db, self.owner_id, tab=self.target)


def _attach_heroes_tabs(
    view: discord.ui.View, db: Database, owner_id: int, current: str,
) -> None:
    for tab in ("roster", "summon", "shards"):
        view.add_item(HeroesTabButton(db, owner_id, tab, current))
    view.add_item(BackToHubButton(db, owner_id))


async def _open_heroes_tab(
    interaction: discord.Interaction,
    db: Database,
    owner_id: int,
    *,
    tab: str,
) -> None:
    """Build the right sub-view for the requested tab and swap in place."""
    if tab == "roster":
        from wagame.cogs.heroes import HeroesView, _build_page_embeds, _fetch_owned
        rows = await _fetch_owned(db, owner_id)
        view = HeroesView(db, owner_id, total=len(rows))
        _attach_heroes_tabs(view, db, owner_id, current="roster")
        embeds = _build_page_embeds(interaction.user, rows, page=0)
        await interaction.response.edit_message(embeds=embeds, view=view)
        return

    if tab == "summon":
        from wagame.cogs.summon import (
            SummonView,
            _fetch_hero_by_id,
            _render_embed,
            fetch_roster,
        )
        hero_ids = await fetch_roster(db)
        if not hero_ids:
            embed = await _render_summon_picker_embed(db, interaction.user)
            view = _BackOnlyView(db, owner_id)
            await interaction.response.edit_message(embed=embed, view=view)
            return
        view = SummonView(db, owner_id, hero_ids, index=0)
        _attach_heroes_tabs(view, db, owner_id, current="summon")
        hero_row = await _fetch_hero_by_id(db, view.current_hero_id)
        embed = await _render_embed(
            db, interaction.user, hero_row,
            index=view.index, total=len(hero_ids),
        )
        await interaction.response.edit_message(embed=embed, view=view)
        return

    # tab == "shards"
    from wagame.cogs.inventory import render_inventory_embed
    embed = await render_inventory_embed(db, interaction.user)
    view = discord.ui.View(timeout=15 * 60)
    _attach_heroes_tabs(view, db, owner_id, current="shards")
    await interaction.response.edit_message(embed=embed, view=view)


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
    def __init__(
        self,
        db: Database,
        owner_id: int,
        is_admin: bool = False,
        has_sighting: bool = False,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.is_admin = is_admin
        if not is_admin:
            # The Admin button is declared statically below; strip it for
            # non-owners so it doesn't show up in the panel at all.
            self.remove_item(self.open_admin)
        if not has_sighting:
            # Hide the sighting button when nothing's active — the DM is
            # the entry point when a real sighting fires.
            self.remove_item(self.open_sighting)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your hub.", ephemeral=True
            )
            return False
        return True

    def _back(self) -> BackToHubButton:
        return BackToHubButton(self.db, self.owner_id)

    @discord.ui.button(label="Monster Hunting", emoji="🦌", style=discord.ButtonStyle.danger, row=0)
    async def open_hunt(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.hunt import HuntView, _render_embed
        view = HuntView(self.db, self.owner_id)
        await view.initialize()
        view.add_item(self._back())
        embed = await _render_embed(
            self.db, interaction.user, view.selected_level,
            view.selected_hero_id, view.selected_support_hero_id,
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="Resource Gathering", emoji="⛏️",
        style=discord.ButtonStyle.primary, row=0,
    )
    async def open_gather(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.gather import GatherView, _render_embed
        view = GatherView(self.db, self.owner_id)
        await view.initialize()
        view.add_item(self._back())
        embed = await _render_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=view)

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
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Summon Troops", emoji="🏰", style=discord.ButtonStyle.primary, row=0)
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
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Heroes", emoji="🎴", style=discord.ButtonStyle.secondary, row=1)
    async def open_heroes(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await _open_heroes_tab(interaction, self.db, self.owner_id, tab="roster")

    @discord.ui.button(label="My Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def open_profile(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.profile import _profile_embed
        player = await self.db.get_or_create_player(self.owner_id)
        embed = _profile_embed(interaction.user, player)
        view = _BackOnlyView(self.db, self.owner_id)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Vault", emoji="🗝", style=discord.ButtonStyle.success, row=1)
    async def open_vault(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.vault import VaultView, render_embed
        view = VaultView(self.db, self.owner_id)
        view.add_item(self._back())
        embed = await render_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = await render_hub_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Bestiary", emoji="📜", style=discord.ButtonStyle.secondary, row=2)
    async def open_bestiary(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.bestiary import BestiaryView, render_embed
        view = BestiaryView(self.db, self.owner_id)
        view.add_item(self._back())
        embed = await render_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Council", emoji="🏛", style=discord.ButtonStyle.secondary, row=2)
    async def open_council(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.council import CouncilView, _render_council_embed
        view = CouncilView(self.db, self.owner_id)
        view.add_item(self._back())
        embed = await _render_council_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Sighting", emoji="🌙", style=discord.ButtonStyle.secondary, row=2)
    async def open_sighting(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        from wagame.cogs.sightings import (
            SightingView,
            _active_sighting,
            _panel_embed,
        )
        row = await _active_sighting(self.db, self.owner_id)
        if row is None:
            embed = discord.Embed(
                title="🌙 No active sighting",
                description=(
                    "Sightings appear at random (6-24h apart). When one fires "
                    "you'll get a DM."
                ),
                color=NEUTRAL_COLOR,
            )
            view = _BackOnlyView(self.db, self.owner_id)
            await interaction.response.edit_message(embed=embed, view=view)
            return
        view = SightingView(self.db, self.owner_id, int(row["id"]))
        await view.initialize()
        view.add_item(self._back())
        embed = await _panel_embed(
            self.db, interaction.user, row, view.selected_hero_id
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Admin", emoji="🛠", style=discord.ButtonStyle.danger, row=2)
    async def open_admin(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if not await interaction.client.is_owner(interaction.user):
            await interaction.response.send_message(
                "Owner only.", ephemeral=True
            )
            return
        from wagame.cogs.admin import AdminHubView, render_admin_hub_embed
        view = AdminHubView(self.db, self.owner_id)
        view.add_item(self._back())
        embed = render_admin_hub_embed()
        await interaction.response.edit_message(embed=embed, view=view)


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
        is_admin = await interaction.client.is_owner(interaction.user)
        has_sighting = await _has_active_sighting(self.db, interaction.user.id)
        view = HubView(
            self.db, interaction.user.id,
            is_admin=is_admin, has_sighting=has_sighting,
        )
        embed = await render_hub_embed(self.db, interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HubCog(bot))
