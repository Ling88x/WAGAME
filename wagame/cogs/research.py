"""Research tree — `/research` panel + auto-claim of finished jobs.

Nodes are static (wagame.research_data). One job per player at a time
(PK on research_jobs). When the timer ends the bonus is applied lazily
the next time a DB-touching command runs.
"""

from __future__ import annotations

import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.research import format_duration, plan_research, prereq_met
from wagame.research_data import NODES, TRACK_LABEL, ResearchNode, get_node
from wagame.ui import Flash, apply_flash

# Whitelist of columns the Research system is allowed to mutate. Defensive:
# `effect_column` flows from a static catalog but we still gate every UPDATE
# through this set so a typo there can't smuggle arbitrary SQL.
ALLOWED_EFFECT_COLUMNS = frozenset(
    {
        "march_capacity",
        "unlocked_tier",
        "training_queue_cap",
        "training_speed_boost_pct",
        "gather_yield_pct",
        "gather_speed_pct",
        "troop_attack_pct",
        "troop_hp_pct",
    }
)


# -- DB helpers -------------------------------------------------------------


async def _fetch_player(db: Database, user_id: int) -> Any:
    async with db.conn.execute(
        "SELECT * FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_levels(db: Database, user_id: int) -> dict[str, int]:
    async with db.conn.execute(
        "SELECT node_codename, level FROM player_research WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {row["node_codename"]: int(row["level"]) for row in rows}


async def _fetch_job(db: Database, user_id: int) -> Any:
    async with db.conn.execute(
        "SELECT * FROM research_jobs WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _apply_completed_level(
    db: Database, user_id: int, node: ResearchNode, new_level: int
) -> None:
    """Bump player_research.level and the relevant player column."""
    if node.effect_column not in ALLOWED_EFFECT_COLUMNS:
        raise RuntimeError(
            f"Research node {node.codename!r} targets disallowed column "
            f"{node.effect_column!r}; refusing to apply."
        )

    await db.conn.execute(
        """
        INSERT INTO player_research (discord_user_id, node_codename, level)
        VALUES (?, ?, ?)
        ON CONFLICT(discord_user_id, node_codename) DO UPDATE SET
            level = excluded.level
        """,
        (user_id, node.codename, new_level),
    )
    # Column name is whitelisted above, so direct interpolation is safe.
    await db.conn.execute(
        f"UPDATE players SET {node.effect_column} = {node.effect_column} + ? "
        "WHERE discord_user_id = ?",
        (node.effect_per_level, user_id),
    )


async def claim_finished_research(
    db: Database, user_id: int
) -> tuple[str, int] | None:
    """If the player's job finished, apply it. Returns (node_name, new_level)."""
    now = int(time.time())
    async with db.conn.execute(
        """
        SELECT node_codename, target_level
        FROM research_jobs
        WHERE discord_user_id = ? AND finishes_at <= ?
        """,
        (user_id, now),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None

    node = get_node(row["node_codename"])
    if node is None:
        # Stale row pointing to a node that no longer exists — just drop it.
        await db.conn.execute(
            "DELETE FROM research_jobs WHERE discord_user_id = ?", (user_id,)
        )
        await db.conn.commit()
        return None

    new_level = int(row["target_level"])
    await _apply_completed_level(db, user_id, node, new_level)
    await db.conn.execute(
        "DELETE FROM research_jobs WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()
    return node.name, new_level


async def start_research(db: Database, user_id: int, node_codename: str) -> Flash:
    """Spend gold, schedule a research job for the next level."""
    node = get_node(node_codename)
    if node is None:
        return Flash.err(f"Unknown research node `{node_codename}`.")

    # Auto-claim so a finished job can't block a new one.
    await claim_finished_research(db, user_id)

    if await _fetch_job(db, user_id) is not None:
        return Flash.err("A research project is already in progress.")

    player = await _fetch_player(db, user_id)
    levels = await _fetch_levels(db, user_id)
    current = levels.get(node.codename, 0)

    if current >= node.max_level:
        return Flash.err(f"{node.name} is already maxed (level {node.max_level}).")

    if node.requires is not None:
        prereq_level = levels.get(node.requires, 0)
        if not prereq_met(node, prereq_level):
            prereq_node = get_node(node.requires)
            prereq_name = prereq_node.name if prereq_node else node.requires
            return Flash.err(
                f"{node.name} requires {prereq_name} level {node.requires_level}."
            )

    target_level = current + 1
    plan = plan_research(node, target_level)
    gold_have = int(player["gold"])
    if plan.gold_cost > gold_have:
        return Flash.err(
            f"Not enough gold — need {plan.gold_cost:,}, have {gold_have:,}."
        )

    now = int(time.time())
    await db.conn.execute(
        "UPDATE players SET gold = gold - ? WHERE discord_user_id = ?",
        (plan.gold_cost, user_id),
    )
    await db.conn.execute(
        """
        INSERT INTO research_jobs (discord_user_id, node_codename, target_level,
                                   started_at, finishes_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, node.codename, target_level, now, now + plan.total_seconds),
    )
    await db.conn.commit()
    return Flash.ok(
        f"Researching {node.name} Lv {target_level} — "
        f"{format_duration(plan.total_seconds)}, cost {plan.gold_cost:,} gold."
    )


# -- rendering --------------------------------------------------------------


def _node_status_line(node: ResearchNode, current: int, prereq_level: int) -> str:
    locked = not prereq_met(node, prereq_level)
    maxed = current >= node.max_level
    badge = "🔒" if locked and not maxed else ("✅" if maxed else "•")
    body = f"{badge} **{node.name}** — Lv {current}/{node.max_level}"
    if locked and not maxed:
        prereq_node = get_node(node.requires) if node.requires else None
        prereq_name = prereq_node.name if prereq_node else node.requires
        body += f"  _(requires {prereq_name} Lv {node.requires_level})_"
    return body


async def _render_embed(
    db: Database,
    user: discord.abc.User,
    selected_codename: str | None,
    flash: Flash | None = None,
) -> discord.Embed:
    player = await _fetch_player(db, user.id)
    levels = await _fetch_levels(db, user.id)
    job = await _fetch_job(db, user.id)

    embed = discord.Embed(title="🔬 Research")
    embed.set_thumbnail(url=user.display_avatar.url)
    apply_flash(embed, flash)
    embed.add_field(name="💰 Gold", value=f"{player['gold']:,}", inline=True)

    if job is not None:
        node = get_node(job["node_codename"])
        name = node.name if node else job["node_codename"]
        finishes_at = int(job["finishes_at"])
        now = int(time.time())
        when = "done!" if now >= finishes_at else f"ready <t:{finishes_at}:R>"
        embed.add_field(
            name="In progress",
            value=f"{name} -> Lv {int(job['target_level'])} — {when}",
            inline=False,
        )
    else:
        embed.add_field(
            name="In progress",
            value="Idle — pick a node and start researching.",
            inline=False,
        )

    by_track: dict[str, list[ResearchNode]] = {}
    for node in NODES:
        by_track.setdefault(node.track, []).append(node)
    for track in ("economy", "military", "logistics"):
        if track not in by_track:
            continue
        nodes = sorted(by_track[track], key=lambda n: n.sort_order)
        lines = []
        for node in nodes:
            current = levels.get(node.codename, 0)
            prereq_level = levels.get(node.requires, 0) if node.requires else 0
            lines.append(_node_status_line(node, current, prereq_level))
        embed.add_field(name=TRACK_LABEL[track], value="\n".join(lines), inline=False)

    if selected_codename:
        node = get_node(selected_codename)
        if node is not None:
            current = levels.get(node.codename, 0)
            prereq_level = (
                levels.get(node.requires, 0) if node.requires else 0
            )
            if current >= node.max_level:
                detail = f"{node.name} is **maxed** at Lv {node.max_level}."
            elif not prereq_met(node, prereq_level):
                prereq_node = get_node(node.requires) if node.requires else None
                prereq_name = prereq_node.name if prereq_node else node.requires
                detail = (
                    f"Locked — requires {prereq_name} Lv {node.requires_level}."
                )
            else:
                target = current + 1
                plan = plan_research(node, target)
                detail = (
                    f"{node.description}\n"
                    f"Next: Lv {target} — cost {plan.gold_cost:,} gold, "
                    f"time {format_duration(plan.total_seconds)}\n"
                    f"After completion: total bonus "
                    f"{node.effect_per_level * target}"
                    f"{'%' if node.effect_column.endswith('_pct') else ''}."
                )
            embed.add_field(name=f"Selected: {node.name}", value=detail, inline=False)

    embed.set_footer(text="Effects auto-apply when the timer ends.")
    return embed


# -- view -------------------------------------------------------------------


class NodeSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label=node.name,
                value=node.codename,
                description=f"{TRACK_LABEL[node.track]} · max Lv {node.max_level}",
            )
            for node in sorted(NODES, key=lambda n: n.sort_order)
        ]
        super().__init__(
            placeholder="Pick a research node…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ResearchView = self.view  # type: ignore[assignment]
        view.selected_codename = self.values[0]
        await view.refresh(interaction)


class ResearchView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.selected_codename: str | None = None
        self.add_item(NodeSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your research panel.", ephemeral=True
            )
            return False
        return True

    async def refresh(
        self, interaction: discord.Interaction, flash: Flash | None = None
    ) -> None:
        await claim_finished_research(self.db, interaction.user.id)
        embed = await _render_embed(
            self.db, interaction.user, self.selected_codename, flash
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(
        label="Start Research", emoji="🔬", style=discord.ButtonStyle.success, row=1
    )
    async def start(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if not self.selected_codename:
            await self.refresh(
                interaction, flash=Flash.info("Pick a node from the dropdown first.")
            )
            return
        result = await start_research(
            self.db, interaction.user.id, self.selected_codename
        )
        await self.refresh(interaction, flash=result)

    @discord.ui.button(
        label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1
    )
    async def refresh_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.refresh(interaction)


# -- cog --------------------------------------------------------------------


class ResearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="research", description="Open the research panel.")
    async def research(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        claimed = await claim_finished_research(self.db, interaction.user.id)
        flash: Flash | None = None
        if claimed:
            name, level = claimed
            flash = Flash.ok(f"Research complete: {name} Lv {level}.")

        view = ResearchView(self.db, interaction.user.id)
        embed = await _render_embed(self.db, interaction.user, None, flash)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ResearchCog(bot))
