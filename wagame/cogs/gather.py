"""/gather — start timed gathers and claim resources via an ephemeral panel.

Each gather rides a commander hero (PR #10). The hero is locked for the
duration of the march: it can't be picked for another `/gather` or
`/hunt` until the row is claimed and removed. Hero `march_speed_pct`
shortens the gather duration on top of any research speed bonus.

Persistent UI shape unchanged: single ephemeral embed + buttons + a
hero Select. Buttons edit in place; `/gather` re-opened spawns a fresh
panel.
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.gather import (
    GATHER_DURATION_SECONDS,
    MAX_SLOTS,
    Resource,
    is_finished,
    roll_gather,
    scaled_gather_duration,
)
from wagame.game.hero_levels import march_speed_pct
from wagame.ui import Flash, apply_flash

RESOURCE_EMOJI: dict[str, str] = {"gold": "💰", "food": "🍞", "wood": "🌲"}


class GatherCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="gather", description="Open your gathering panel.")
    async def gather(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        view = GatherView(self.db, interaction.user.id)
        await view.initialize()
        embed = await _render_embed(self.db, interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# -- DB helpers -------------------------------------------------------------


async def _fetch_player(db: Database, user_id: int):
    async with db.conn.execute(
        "SELECT * FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_marches(db: Database, user_id: int):
    async with db.conn.execute(
        """
        SELECT m.*, h.name AS hero_name, h.rarity AS hero_rarity
        FROM marches m
        LEFT JOIN heroes h ON h.id = m.hero_id
        WHERE m.discord_user_id = ?
        ORDER BY m.finishes_at ASC
        """,
        (user_id,),
    ) as cur:
        return await cur.fetchall()


async def _fetch_owned_heroes(db: Database, user_id: int):
    async with db.conn.execute(
        """
        SELECT h.id, h.name, h.rarity, o.level
        FROM owned_heroes o
        JOIN heroes h ON h.id = o.hero_id
        WHERE o.discord_user_id = ?
        ORDER BY o.level DESC, h.name
        """,
        (user_id,),
    ) as cur:
        return await cur.fetchall()


async def _busy_hero_ids(db: Database, user_id: int) -> set[int]:
    """Heroes locked in an active gather or hunt march."""
    busy: set[int] = set()
    async with db.conn.execute(
        "SELECT DISTINCT hero_id FROM marches "
        "WHERE discord_user_id = ? AND hero_id IS NOT NULL",
        (user_id,),
    ) as cur:
        for row in await cur.fetchall():
            busy.add(int(row["hero_id"]))
    async with db.conn.execute(
        "SELECT DISTINCT hero_id FROM hunt_marches "
        "WHERE discord_user_id = ? AND resolved = 0 AND hero_id IS NOT NULL",
        (user_id,),
    ) as cur:
        for row in await cur.fetchall():
            busy.add(int(row["hero_id"]))
    return busy


async def _start_gather(
    db: Database,
    user_id: int,
    resource: Resource,
    hero_id: int | None,
) -> Flash:
    player = await _fetch_player(db, user_id)
    capacity = int(player["march_capacity"])
    marches = await _fetch_marches(db, user_id)
    if len(marches) >= capacity:
        return Flash.err(
            f"All {capacity} march slot(s) busy — claim a finished gather first."
        )
    if hero_id is None:
        return Flash.err("Pick a hero from the dropdown first.")

    # Confirm ownership + freshness.
    async with db.conn.execute(
        "SELECT o.level FROM owned_heroes o "
        "WHERE o.discord_user_id = ? AND o.hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        hero_row = await cur.fetchone()
    if hero_row is None:
        return Flash.err("That hero isn't in your roster.")

    busy = await _busy_hero_ids(db, user_id)
    if hero_id in busy:
        return Flash.err("That hero is already in another march.")

    roll = roll_gather(resource)
    yield_pct = int(player["gather_yield_pct"])
    speed_pct = int(player["gather_speed_pct"])
    hero_speed = march_speed_pct(int(hero_row["level"]))
    boosted_base = int(roll.base * (100 + yield_pct) / 100)
    duration = scaled_gather_duration(
        GATHER_DURATION_SECONDS,
        hero_march_speed_pct=hero_speed,
        research_speed_pct=speed_pct,
    )
    now = int(time.time())
    finishes_at = now + duration
    await db.conn.execute(
        """
        INSERT INTO marches (discord_user_id, resource, started_at, finishes_at,
                             yield_amount, crit, hero_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, resource, now, finishes_at, boosted_base,
            1 if roll.crit else 0, hero_id,
        ),
    )
    await db.conn.commit()
    return Flash.ok(f"Sent a march to gather {resource}.")


async def _claim_ready(db: Database, user_id: int) -> tuple[int, dict[Resource, int], int]:
    """Claim every finished march. Returns (count, totals_by_resource, crits)."""
    now = int(time.time())
    async with db.conn.execute(
        "SELECT * FROM marches WHERE discord_user_id = ? AND finishes_at <= ?",
        (user_id, now),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        return 0, {}, 0

    totals: dict[Resource, int] = {"gold": 0, "food": 0, "wood": 0}
    crits = 0
    ids: list[int] = []
    for row in rows:
        resource: Resource = row["resource"]
        crit = bool(row["crit"])
        amount = int(row["yield_amount"]) * (2 if crit else 1)
        totals[resource] += amount
        if crit:
            crits += 1
        ids.append(int(row["id"]))

    await db.conn.execute(
        """
        UPDATE players SET
            gold = gold + ?,
            food = food + ?,
            wood = wood + ?
        WHERE discord_user_id = ?
        """,
        (totals["gold"], totals["food"], totals["wood"], user_id),
    )
    placeholders = ",".join("?" * len(ids))
    await db.conn.execute(f"DELETE FROM marches WHERE id IN ({placeholders})", ids)
    await db.conn.commit()
    return len(rows), totals, crits


# -- rendering --------------------------------------------------------------


async def _render_embed(db: Database, user: discord.abc.User) -> discord.Embed:
    player = await _fetch_player(db, user.id)
    marches = await _fetch_marches(db, user.id)
    capacity = int(player["march_capacity"])
    now = int(time.time())

    embed = discord.Embed(title="⛏️ Gathering")
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="💰 Gold", value=f"{player['gold']:,}", inline=True)
    embed.add_field(name="🍞 Food", value=f"{player['food']:,}", inline=True)
    embed.add_field(name="🌲 Wood", value=f"{player['wood']:,}", inline=True)

    in_use = len(marches)
    embed.add_field(
        name="Marches",
        value=f"{in_use}/{capacity} in use (cap {MAX_SLOTS} via research)",
        inline=False,
    )

    if marches:
        lines: list[str] = []
        for i, row in enumerate(marches, start=1):
            emoji = RESOURCE_EMOJI.get(row["resource"], "•")
            finishes_at = int(row["finishes_at"])
            hero_tag = f" · 🪄 {row['hero_name']}" if row["hero_name"] else ""
            if is_finished(finishes_at, now):
                lines.append(
                    f"`{i}.` {emoji} {row['resource']}{hero_tag} — "
                    "**ready to claim**"
                )
            else:
                lines.append(
                    f"`{i}.` {emoji} {row['resource']}{hero_tag} — "
                    f"claims <t:{finishes_at}:R>"
                )
        embed.add_field(name="In progress", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="In progress",
            value="Pick a hero below, then click a resource to send a march.",
            inline=False,
        )

    embed.set_footer(
        text=(
            f"Base gather: {GATHER_DURATION_SECONDS // 60} min · "
            "hero march-speed shortens it (capped at 80%)."
        )
    )
    return embed


# -- hero selector ----------------------------------------------------------


class HeroSelect(discord.ui.Select):
    def __init__(self, owned, busy: set[int], current: int | None) -> None:
        options: list[discord.SelectOption] = []
        for row in owned[:25]:
            hero_id = int(row["id"])
            busy_marker = " · busy" if hero_id in busy else ""
            options.append(
                discord.SelectOption(
                    label=f"{row['name']} · Lv {row['level']}{busy_marker}",
                    description=row["rarity"].capitalize(),
                    value=str(hero_id),
                    default=(current is not None and hero_id == current),
                )
            )
        if not options:
            options = [
                discord.SelectOption(
                    label="No heroes owned",
                    description="Use /summon to recruit.",
                    value="none",
                )
            ]
            super().__init__(
                placeholder="No heroes owned",
                options=options,
                row=2,
                disabled=True,
            )
            return
        super().__init__(placeholder="Gather hero", options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: GatherView = self.view  # type: ignore[assignment]
        view.selected_hero_id = int(self.values[0])
        await view._rebuild()
        await view._refresh(interaction)


# -- view -------------------------------------------------------------------


class GatherView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.selected_hero_id: int | None = None
        self._hero_select: HeroSelect | None = None

    async def initialize(self) -> None:
        owned = await _fetch_owned_heroes(self.db, self.owner_id)
        busy = await _busy_hero_ids(self.db, self.owner_id)
        # Auto-pick first free hero so the panel is usable straight away.
        for row in owned:
            if int(row["id"]) not in busy:
                self.selected_hero_id = int(row["id"])
                break
        await self._rebuild(owned, busy)

    async def _rebuild(self, owned=None, busy=None) -> None:
        if owned is None:
            owned = await _fetch_owned_heroes(self.db, self.owner_id)
        if busy is None:
            busy = await _busy_hero_ids(self.db, self.owner_id)
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        self._hero_select = HeroSelect(owned, busy, self.selected_hero_id)
        self.add_item(self._hero_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your gathering panel.", ephemeral=True
            )
            return False
        return True

    async def _refresh(
        self, interaction: discord.Interaction, flash: Flash | None = None
    ) -> None:
        await self._rebuild()
        embed = await _render_embed(self.db, interaction.user)
        apply_flash(embed, flash)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def _start(
        self, interaction: discord.Interaction, resource: Resource
    ) -> None:
        result = await _start_gather(
            self.db, interaction.user.id, resource, self.selected_hero_id
        )
        # Always surface the flash so the player sees errors and successes.
        await self._refresh(interaction, flash=result)

    @discord.ui.button(label="Gold", emoji="💰", style=discord.ButtonStyle.primary, row=0)
    async def start_gold(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._start(interaction, "gold")

    @discord.ui.button(label="Food", emoji="🍞", style=discord.ButtonStyle.primary, row=0)
    async def start_food(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._start(interaction, "food")

    @discord.ui.button(label="Wood", emoji="🌲", style=discord.ButtonStyle.primary, row=0)
    async def start_wood(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._start(interaction, "wood")

    @discord.ui.button(label="Claim Ready", emoji="📦", style=discord.ButtonStyle.success, row=1)
    async def claim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        count, totals, crits = await _claim_ready(self.db, interaction.user.id)
        if count == 0:
            await self._refresh(interaction, flash=Flash.info("Nothing finished yet."))
            return
        parts = [f"{v:,} {k}" for k, v in totals.items() if v]
        msg = f"Claimed {count} march(es): " + ", ".join(parts)
        if crits:
            msg += f" — {crits} crit{'s' if crits > 1 else ''}! ✨"
        await self._refresh(interaction, flash=Flash.ok(msg))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._refresh(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GatherCog(bot))
