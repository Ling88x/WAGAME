"""/bestiary — per-player log of every tenebral encountered or slain.

Hooked into the hunt and sighting engagement helpers so every attack
bumps `encounter_count` and every kill bumps `slain_count`. The panel
renders all 12 tenebral slots: documented entries show kill / encounter
counts plus a relative timestamp on the last kill, undocumented slots
stay as "???".

Completion percent shows up in `/wa` so the long-term goal is visible
without opening the panel.
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.bestiary import (
    TENEBRAL_SLOT_COUNT,
    BestiaryLine,
    completion_pct,
    render_line,
)
from wagame.game.hunt import TENEBRAL_TABLE
from wagame.ui import NEUTRAL_COLOR

# -- recording ------------------------------------------------------------


async def record_encounter(
    db: Database,
    *,
    user_id: int,
    mob_kind: str,
    mob_level: int,
    killed: bool,
    now: int | None = None,
) -> None:
    """Bump (or insert) the bestiary entry for this (player, kind, level).

    Idempotent on the (player, kind, level) primary key — concurrent
    callers race on the upsert but each one only adds 1 to the counters
    they intended, which is the semantics we want.
    """
    ts = now if now is not None else int(time.time())
    slain_delta = 1 if killed else 0
    await db.conn.execute(
        """
        INSERT INTO bestiary_entries (
            discord_user_id, mob_kind, mob_level,
            slain_count, encounter_count,
            first_seen_at, last_seen_at
        )
        VALUES (?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(discord_user_id, mob_kind, mob_level) DO UPDATE SET
            slain_count = slain_count + excluded.slain_count,
            encounter_count = encounter_count + 1,
            last_seen_at = excluded.last_seen_at
        """,
        (user_id, mob_kind, mob_level, slain_delta, ts, ts),
    )
    await db.conn.commit()


# -- fetch ----------------------------------------------------------------


async def fetch_lines(db: Database, user_id: int) -> list[BestiaryLine]:
    """Return one BestiaryLine per tenebral slot (1-12), filled or blank."""
    async with db.conn.execute(
        "SELECT mob_level, slain_count, encounter_count, "
        "first_seen_at, last_seen_at "
        "FROM bestiary_entries "
        "WHERE discord_user_id = ? AND mob_kind = 'tenebral'",
        (user_id,),
    ) as cur:
        by_level = {int(r["mob_level"]): r for r in await cur.fetchall()}

    lines: list[BestiaryLine] = []
    for tenebral in TENEBRAL_TABLE:
        row = by_level.get(tenebral.level)
        if row is None:
            lines.append(
                BestiaryLine(
                    level=tenebral.level,
                    name=tenebral.name,
                    slain=0,
                    encounters=0,
                    first_seen_at=0,
                    last_seen_at=0,
                )
            )
        else:
            lines.append(
                BestiaryLine(
                    level=tenebral.level,
                    name=tenebral.name,
                    slain=int(row["slain_count"]),
                    encounters=int(row["encounter_count"]),
                    first_seen_at=int(row["first_seen_at"]),
                    last_seen_at=int(row["last_seen_at"]),
                )
            )
    return lines


async def documented_count(db: Database, user_id: int) -> int:
    async with db.conn.execute(
        "SELECT COUNT(*) AS n FROM bestiary_entries "
        "WHERE discord_user_id = ? AND mob_kind = 'tenebral' "
        "AND encounter_count > 0",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return int(row["n"] or 0)


# -- rendering ------------------------------------------------------------


async def render_embed(db: Database, user: discord.abc.User) -> discord.Embed:
    lines = await fetch_lines(db, user.id)
    documented = sum(1 for line in lines if line.documented)
    pct = completion_pct(documented, TENEBRAL_SLOT_COUNT)
    total_slain = sum(line.slain for line in lines)

    embed = discord.Embed(
        title=f"📜 {user.display_name}'s Bestiary",
        color=NEUTRAL_COLOR,
        description=(
            f"Documented **{documented}/{TENEBRAL_SLOT_COUNT}** ({pct}%). "
            f"Tenebrals slain: **{total_slain:,}**."
        ),
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    embed.add_field(
        name="Tenebrals",
        value="\n".join(render_line(line) for line in lines),
        inline=False,
    )
    embed.set_footer(
        text=(
            "Every chip counts as an encounter. Kills add to the slain "
            "tally. Locked slots reveal as you face them."
        )
    )
    return embed


# -- view -----------------------------------------------------------------


class BestiaryView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your bestiary.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = await render_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self)


# -- cog ------------------------------------------------------------------


class BestiaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="bestiary",
        description="View your log of every tenebral you've encountered.",
    )
    async def bestiary(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        embed = await render_embed(self.db, interaction.user)
        view = BestiaryView(self.db, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BestiaryCog(bot))
