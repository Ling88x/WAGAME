"""Hero Bonds cog — `/bonds` panel + the `record_bond` hook hunt calls.

Hunt's attack handler reports `(primary_hero_id, support_hero_id)` and
whether the engagement killed; this module canonicalises the pair,
looks up affinity multipliers from the heroes' shared traits, and
upserts the per-player bond row. The active march power buff
(`bond_bonus_pct_for`) is also exposed so hunt can apply it on every
engagement.
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.bonds import (
    BondLine,
    affinity_multiplier,
    canonical_pair,
    next_threshold,
    points_for_engagement,
    tier_for,
)
from wagame.ui import NEUTRAL_COLOR

log = logging.getLogger(__name__)


# -- recording --------------------------------------------------------


async def _affinity_for_pair(
    db: Database, hero_a_id: int, hero_b_id: int,
) -> float:
    async with db.conn.execute(
        "SELECT id, terrain, house, element FROM heroes WHERE id IN (?, ?)",
        (hero_a_id, hero_b_id),
    ) as cur:
        rows = await cur.fetchall()
    if len(rows) != 2:
        return 1.0
    by_id = {int(r["id"]): r for r in rows}
    a = by_id.get(hero_a_id)
    b = by_id.get(hero_b_id)
    if a is None or b is None:
        return 1.0
    return affinity_multiplier(
        a["terrain"], b["terrain"],
        a["house"],   b["house"],
        a["element"], b["element"],
    )


async def record_bond(
    db: Database,
    user_id: int,
    primary_hero_id: int | None,
    support_hero_id: int | None,
    *,
    killed: bool,
) -> int:
    """Credit a bond point grant. Returns the points actually awarded.

    No-op if no support hero, or if the support is the same as primary.
    """
    if primary_hero_id is None or support_hero_id is None:
        return 0
    if primary_hero_id == support_hero_id:
        return 0
    a, b = canonical_pair(primary_hero_id, support_hero_id)
    base = points_for_engagement(killed)
    mult = await _affinity_for_pair(db, a, b)
    points = max(1, int(base * mult))
    now = int(time.time())
    await db.conn.execute(
        """
        INSERT INTO hero_bonds
          (discord_user_id, hero_a_id, hero_b_id, points, last_bonded_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(discord_user_id, hero_a_id, hero_b_id) DO UPDATE SET
            points = points + excluded.points,
            last_bonded_at = excluded.last_bonded_at
        """,
        (user_id, a, b, points, now),
    )
    await db.conn.commit()
    return points


async def bond_bonus_pct_for(
    db: Database,
    user_id: int,
    primary_hero_id: int | None,
    support_hero_id: int | None,
) -> int:
    """Return the march-power percent the active pair currently grants."""
    if primary_hero_id is None or support_hero_id is None:
        return 0
    if primary_hero_id == support_hero_id:
        return 0
    a, b = canonical_pair(primary_hero_id, support_hero_id)
    async with db.conn.execute(
        "SELECT points FROM hero_bonds "
        "WHERE discord_user_id = ? AND hero_a_id = ? AND hero_b_id = ?",
        (user_id, a, b),
    ) as cur:
        row = await cur.fetchone()
    points = int(row["points"]) if row else 0
    return tier_for(points).bonus_pct


# -- fetch ------------------------------------------------------------


async def fetch_top_bonds(
    db: Database, user_id: int, limit: int = 10,
) -> list[BondLine]:
    async with db.conn.execute(
        """
        SELECT b.hero_a_id, b.hero_b_id, b.points, b.last_bonded_at,
               ha.name AS a_name, hb.name AS b_name
        FROM hero_bonds b
        JOIN heroes ha ON ha.id = b.hero_a_id
        JOIN heroes hb ON hb.id = b.hero_b_id
        WHERE b.discord_user_id = ?
        ORDER BY b.points DESC
        LIMIT ?
        """,
        (user_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    out: list[BondLine] = []
    for r in rows:
        tier = tier_for(int(r["points"]))
        out.append(
            BondLine(
                hero_a_id=int(r["hero_a_id"]),
                hero_a_name=r["a_name"],
                hero_b_id=int(r["hero_b_id"]),
                hero_b_name=r["b_name"],
                points=int(r["points"]),
                level=tier.level,
                bonus_pct=tier.bonus_pct,
                last_bonded_at=int(r["last_bonded_at"]),
            )
        )
    return out


# -- /bonds panel -----------------------------------------------------


async def render_bonds_embed(
    db: Database, user: discord.abc.User,
) -> discord.Embed:
    bonds = await fetch_top_bonds(db, user.id, limit=10)
    embed = discord.Embed(
        title=f"🔗 {user.display_name}'s Hero Bonds",
        color=NEUTRAL_COLOR,
        description=(
            "Pair two heroes in a `/hunt` march (primary + support) to "
            "build their bond. Shared terrain, house, or element grows "
            "the bond faster."
        ),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    if not bonds:
        embed.add_field(
            name="No bonds yet",
            value=(
                "Open `/hunt`, pick a primary hero AND a support hero, "
                "and the bond starts on the first engagement."
            ),
            inline=False,
        )
        return embed

    lines = []
    for line in bonds:
        next_info = next_threshold(line.points)
        progress = (
            f"→ Lv{next_info[0]} in {next_info[1]} pts"
            if next_info is not None
            else "MAX"
        )
        lines.append(
            f"**{line.hero_a_name}** ⟷ **{line.hero_b_name}** — "
            f"Lv{line.level} (+{line.bonus_pct}% power) · "
            f"{line.points:,} pts · {progress}"
        )
    embed.add_field(
        name="Top bonds",
        value="\n".join(lines),
        inline=False,
    )
    embed.set_footer(
        text=(
            "Bond points: +1 per chip, +3 per kill. Affinity stacks: "
            "terrain x1.5, house x2.0, element x1.3."
        )
    )
    return embed


class BondsView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your bonds panel.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = await render_bonds_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self)


class BondsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="bonds",
        description="View your hero bonds.",
    )
    async def bonds(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        embed = await render_bonds_embed(self.db, interaction.user)
        view = BondsView(self.db, interaction.user.id)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BondsCog(bot))
