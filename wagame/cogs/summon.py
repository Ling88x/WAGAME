"""Summoning Gate — train troops one batch at a time.

`/summon` opens an ephemeral panel. Pick a unit from the dropdown (only
tiers the player has unlocked appear), then click a Train button (1 / 10 /
50 / Max). One job at a time; the PK on `training_jobs` enforces it. When
the timer ends the troops auto-arrive on the next DB-touching command —
no manual claim, per user's call.

`/army` lists owned troops grouped by tier.
"""

from __future__ import annotations

import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.summon import (
    format_duration,
    max_affordable_count,
    plan_training,
)

# Discord caps SelectMenu options at 25 — we only have 8 troops, plenty of room.

ROLE_LABEL = {
    "monster":  "vs Monsters",
    "defender": "Defending",
    "player":   "vs Players",
}


# -- DB helpers -------------------------------------------------------------


async def _fetch_player(db: Database, user_id: int) -> Any:
    async with db.conn.execute(
        "SELECT * FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_troop(db: Database, codename: str) -> Any:
    async with db.conn.execute(
        "SELECT * FROM troops WHERE codename = ?", (codename,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_unlocked_troops(db: Database, max_tier: int) -> list[Any]:
    async with db.conn.execute(
        "SELECT * FROM troops WHERE tier <= ? ORDER BY tier, name", (max_tier,)
    ) as cur:
        return list(await cur.fetchall())


async def _fetch_job(db: Database, user_id: int) -> Any:
    async with db.conn.execute(
        "SELECT * FROM training_jobs WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_owned(db: Database, user_id: int) -> list[Any]:
    async with db.conn.execute(
        """
        SELECT t.codename, t.name, t.tier, t.role, o.count
        FROM owned_troops o
        JOIN troops t ON t.codename = o.troop_codename
        WHERE o.discord_user_id = ? AND o.count > 0
        ORDER BY t.tier, t.name
        """,
        (user_id,),
    ) as cur:
        return list(await cur.fetchall())


async def claim_finished_training(db: Database, user_id: int) -> tuple[str, int] | None:
    """Auto-claim: if the player's job finished, move the count to owned and clear it.

    Returns (troop_name, count) when something was claimed, otherwise None.
    Safe to call on every command — does nothing if the job is still running
    or absent.
    """
    now = int(time.time())
    async with db.conn.execute(
        """
        SELECT j.troop_codename, j.count, t.name
        FROM training_jobs j
        JOIN troops t ON t.codename = j.troop_codename
        WHERE j.discord_user_id = ? AND j.finishes_at <= ?
        """,
        (user_id, now),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None

    codename = row["troop_codename"]
    count = int(row["count"])
    await db.conn.execute(
        """
        INSERT INTO owned_troops (discord_user_id, troop_codename, count)
        VALUES (?, ?, ?)
        ON CONFLICT(discord_user_id, troop_codename) DO UPDATE SET
            count = count + excluded.count
        """,
        (user_id, codename, count),
    )
    await db.conn.execute(
        "DELETE FROM training_jobs WHERE discord_user_id = ?", (user_id,)
    )
    await db.conn.commit()
    return str(row["name"]), count


async def start_training(
    db: Database, user_id: int, troop_codename: str, count: int
) -> tuple[bool, str]:
    """Attempt to start a new batch. Returns (ok, message)."""
    if count <= 0:
        return False, "Count must be at least 1."

    # Auto-claim first so a finished job can't block a new one.
    await claim_finished_training(db, user_id)

    if await _fetch_job(db, user_id) is not None:
        return False, "A training batch is already in progress."

    player = await _fetch_player(db, user_id)
    troop = await _fetch_troop(db, troop_codename)
    if troop is None:
        return False, f"Unknown troop `{troop_codename}`."
    if int(troop["tier"]) > int(player["unlocked_tier"]):
        return False, f"{troop['name']} is locked — unlock T{troop['tier']} via research."

    cap = int(player["training_queue_cap"])
    if count > cap:
        return False, f"Batch size capped at {cap:,} (your queue cap)."

    food_have = int(player["food"])
    food_per_unit = int(troop["food_per_unit"])
    cost = food_per_unit * count
    if cost > food_have:
        return False, f"Not enough food — need {cost:,}, have {food_have:,}."

    plan = plan_training(
        troop_codename=troop_codename,
        count=count,
        food_per_unit=food_per_unit,
        train_seconds_per_unit=int(troop["train_seconds"]),
        speed_boost_pct=int(player["training_speed_boost_pct"]),
    )

    now = int(time.time())
    await db.conn.execute(
        "UPDATE players SET food = food - ? WHERE discord_user_id = ?",
        (plan.food_cost, user_id),
    )
    await db.conn.execute(
        """
        INSERT INTO training_jobs (discord_user_id, troop_codename, count,
                                   started_at, finishes_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, troop_codename, plan.count, now, now + plan.total_seconds),
    )
    await db.conn.commit()
    return True, (
        f"Training {plan.count:,}x {troop['name']} — "
        f"{format_duration(plan.total_seconds)}, cost {plan.food_cost:,} food."
    )


# -- rendering --------------------------------------------------------------


async def _render_embed(
    db: Database,
    user: discord.abc.User,
    selected_codename: str | None,
    flash: str | None = None,
) -> discord.Embed:
    player = await _fetch_player(db, user.id)
    job = await _fetch_job(db, user.id)
    troops = await _fetch_unlocked_troops(db, int(player["unlocked_tier"]))

    embed = discord.Embed(title="Summoning Gate", color=discord.Color.dark_purple())
    embed.set_thumbnail(url=user.display_avatar.url)
    if flash:
        embed.description = f"**{flash}**"

    embed.add_field(name="🍞 Food", value=f"{player['food']:,}", inline=True)
    embed.add_field(
        name="Queue cap",
        value=f"{int(player['training_queue_cap']):,}",
        inline=True,
    )
    speed = int(player["training_speed_boost_pct"])
    embed.add_field(name="Speed boost", value=f"{speed}%" if speed else "—", inline=True)

    embed.add_field(
        name="Unlocked tier",
        value=f"T{int(player['unlocked_tier'])} (raise via research)",
        inline=False,
    )

    if job is not None:
        async with db.conn.execute(
            "SELECT name FROM troops WHERE codename = ?", (job["troop_codename"],)
        ) as cur:
            troop_row = await cur.fetchone()
        name = troop_row["name"] if troop_row else job["troop_codename"]
        left = max(0, int(job["finishes_at"]) - int(time.time()))
        embed.add_field(
            name="In progress",
            value=f"{int(job['count']):,}x {name} — {format_duration(left) or 'done!'}",
            inline=False,
        )
    else:
        embed.add_field(name="In progress", value="Idle — pick a unit and train.", inline=False)

    if selected_codename:
        troop = next((t for t in troops if t["codename"] == selected_codename), None)
        if troop is not None:
            role = ROLE_LABEL.get(troop["role"]) if troop["role"] else None
            bonus = (
                f" (+{int(troop['role_bonus'])} {role})"
                if role and int(troop["role_bonus"])
                else ""
            )
            embed.add_field(
                name=f"Selected: {troop['name']} (T{int(troop['tier'])}){bonus}",
                value=(
                    f"ATK {int(troop['attack'])} · HP {int(troop['hp'])} · "
                    f"Spd {int(troop['march_speed'])} · Carry {float(troop['carry_cap']):g}\n"
                    f"Cost: {int(troop['food_per_unit'])} food/unit · "
                    f"Base time: {format_duration(int(troop['train_seconds']))}/unit"
                ),
                inline=False,
            )

    embed.set_footer(text="Troops auto-arrive in your city when the timer ends.")
    return embed


# -- view -------------------------------------------------------------------


class TroopSelect(discord.ui.Select):
    def __init__(self, troops: list[Any]) -> None:
        options = [
            discord.SelectOption(
                label=f"{t['name']} (T{int(t['tier'])})",
                value=t["codename"],
                description=(
                    f"{int(t['food_per_unit'])} food · "
                    f"{format_duration(int(t['train_seconds']))} each"
                ),
            )
            for t in troops
        ] or [discord.SelectOption(label="No units unlocked", value="__none__")]
        super().__init__(
            placeholder="Pick a unit to train…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
            disabled=not troops,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SummonView = self.view  # type: ignore[assignment]
        if self.values and self.values[0] != "__none__":
            view.selected_codename = self.values[0]
        await view.refresh(interaction)


class SummonView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int, troops: list[Any]) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.selected_codename: str | None = None
        self.add_item(TroopSelect(troops))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your Summoning Gate panel.", ephemeral=True
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, flash: str | None = None) -> None:
        await claim_finished_training(self.db, interaction.user.id)
        embed = await _render_embed(self.db, interaction.user, self.selected_codename, flash)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _train(self, interaction: discord.Interaction, amount: int | str) -> None:
        if not self.selected_codename:
            await self.refresh(interaction, flash="Pick a unit from the dropdown first.")
            return

        # Resolve "max" against current resources, queue cap, and food cost.
        player = await _fetch_player(self.db, interaction.user.id)
        troop = await _fetch_troop(self.db, self.selected_codename)
        if troop is None:
            await self.refresh(interaction, flash="That unit no longer exists.")
            return

        if amount == "max":
            count = max_affordable_count(
                food_available=int(player["food"]),
                food_per_unit=int(troop["food_per_unit"]),
                queue_cap=int(player["training_queue_cap"]),
            )
            if count <= 0:
                await self.refresh(interaction, flash="You can't afford even one unit.")
                return
        else:
            count = int(amount)

        _, msg = await start_training(
            self.db, interaction.user.id, self.selected_codename, count
        )
        await self.refresh(interaction, flash=msg)

    @discord.ui.button(label="Train 1", style=discord.ButtonStyle.primary, row=1)
    async def train_1(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._train(interaction, 1)

    @discord.ui.button(label="Train 10", style=discord.ButtonStyle.primary, row=1)
    async def train_10(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._train(interaction, 10)

    @discord.ui.button(label="Train 50", style=discord.ButtonStyle.primary, row=1)
    async def train_50(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._train(interaction, 50)

    @discord.ui.button(label="Train Max", style=discord.ButtonStyle.success, row=1)
    async def train_max(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._train(interaction, "max")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def refresh_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.refresh(interaction)


# -- cog --------------------------------------------------------------------


class SummonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="summon", description="Open the Summoning Gate panel.")
    async def summon(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        claimed = await claim_finished_training(self.db, interaction.user.id)
        player = await _fetch_player(self.db, interaction.user.id)
        troops = await _fetch_unlocked_troops(self.db, int(player["unlocked_tier"]))
        flash = None
        if claimed:
            name, count = claimed
            flash = f"Training complete: {count:,}x {name} arrived in your city."

        view = SummonView(self.db, interaction.user.id, troops)
        embed = await _render_embed(self.db, interaction.user, None, flash)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="army", description="List the troops in your city.")
    async def army(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        claimed = await claim_finished_training(self.db, interaction.user.id)
        rows = await _fetch_owned(self.db, interaction.user.id)

        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Army",
            color=discord.Color.dark_purple(),
        )
        if claimed:
            name, count = claimed
            embed.description = f"Training complete: **{count:,}x {name}** just arrived."

        if not rows:
            embed.add_field(
                name="No troops yet",
                value="Open `/summon` to train your first batch of Catsiths.",
                inline=False,
            )
        else:
            by_tier: dict[int, list[str]] = {}
            total = 0
            for row in rows:
                tier = int(row["tier"])
                count = int(row["count"])
                total += count
                line = f"`{count:>7,}` {row['name']}"
                role = ROLE_LABEL.get(row["role"]) if row["role"] else None
                if role:
                    line += f" — {role}"
                by_tier.setdefault(tier, []).append(line)
            for tier in sorted(by_tier):
                embed.add_field(
                    name=f"Tier {tier}",
                    value="\n".join(by_tier[tier]),
                    inline=False,
                )
            embed.set_footer(text=f"{total:,} troops total.")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SummonCog(bot))
