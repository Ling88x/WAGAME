"""/summon — summoning panel with a hero slider.

One ephemeral panel that flips through every hero in the catalog with
`◀` / `▶` buttons; the centre `🔮 Summon` button spends gems on whoever
is currently shown. Same panel handles all heroes — no need to type a
name or autocomplete (slash still accepts an optional `hero` arg as a
power-user jump-to).

Heroes are auto-inserted into `owned_heroes` the moment their shard
count crosses 100 — no separate "claim" step. Excess shards keep
accumulating in `hero_shards` for later level-up systems.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from wagame.cogs.heroes import RARITY_COLOR, RARITY_EMOJI, _autocomplete_hero
from wagame.db import Database
from wagame.game.summon import (
    PITY_LIMIT,
    SHARDS_PER_UNLOCK,
    SummonResult,
    roll_summon,
    summon_cost,
)
from wagame.ui import Flash, apply_flash

log = logging.getLogger(__name__)

# Order matches the autocomplete + roster panels: rarity high-to-low,
# then alphabetical by name. Cached on view init so flipping is instant.
_ROSTER_SORT_SQL = """
SELECT id, name, codename, rarity, image_url
FROM heroes
ORDER BY
    CASE rarity
        WHEN 'mythic'    THEN 0
        WHEN 'legendary' THEN 1
        WHEN 'epic'      THEN 2
        WHEN 'rare'      THEN 3
        WHEN 'uncommon'  THEN 4
        WHEN 'common'    THEN 5
        ELSE 6
    END,
    name
"""


# -- DB helpers -------------------------------------------------------------


async def _fetch_hero_by_name(db: Database, needle: str):
    """Resolve a name OR codename into a hero row (case-insensitive)."""
    n = needle.strip().lower()
    async with db.conn.execute(
        "SELECT * FROM heroes WHERE LOWER(name) = ?", (n,)
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        return row
    async with db.conn.execute(
        "SELECT * FROM heroes WHERE codename = ?", (n,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_hero_by_id(db: Database, hero_id: int):
    async with db.conn.execute(
        "SELECT * FROM heroes WHERE id = ?", (hero_id,)
    ) as cur:
        return await cur.fetchone()


async def fetch_roster(db: Database) -> list[int]:
    """All hero ids in display order. Public so the hub button can seed a view."""
    async with db.conn.execute(_ROSTER_SORT_SQL) as cur:
        return [int(r["id"]) for r in await cur.fetchall()]


async def _fetch_shards(db: Database, user_id: int, hero_id: int) -> tuple[int, int]:
    """Return (count, pity) for this user+hero; (0, 0) if no row yet."""
    async with db.conn.execute(
        "SELECT count, pity_pulls FROM hero_shards "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return 0, 0
    return int(row["count"]), int(row["pity_pulls"])


async def _is_owned(db: Database, user_id: int, hero_id: int) -> bool:
    async with db.conn.execute(
        "SELECT 1 FROM owned_heroes WHERE discord_user_id = ? AND hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        return await cur.fetchone() is not None


async def execute_summon(
    db: Database, user_id: int, hero_row
) -> tuple[SummonResult, Flash]:
    """Commit one summon on `hero_row` for `user_id`. Caller pre-checked gems."""
    cost = summon_cost(hero_row["rarity"])
    current, pity = await _fetch_shards(db, user_id, hero_row["id"])
    result = roll_summon(current, pity)

    await db.conn.execute(
        "UPDATE players SET gems = gems - ? WHERE discord_user_id = ?",
        (cost, user_id),
    )
    await db.conn.execute(
        """
        INSERT INTO hero_shards (discord_user_id, hero_id, count, pity_pulls)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
            count      = excluded.count,
            pity_pulls = excluded.pity_pulls
        """,
        (user_id, hero_row["id"], result.new_total, result.new_pity),
    )
    if result.unlocked_now:
        await db.conn.execute(
            """
            INSERT INTO owned_heroes (discord_user_id, hero_id, level)
            VALUES (?, ?, 1)
            ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                dupes_pending = dupes_pending + 1
            """,
            (user_id, hero_row["id"]),
        )
    await db.conn.commit()

    flash = _flash_for_result(result, hero_row["name"])
    return result, flash


def _flash_for_result(result: SummonResult, hero_name: str) -> Flash:
    if result.unlocked_now and result.pity_activated:
        return Flash.ok(
            f"🎁 Pity grants {result.shards_rolled} shards — {hero_name} unlocked!"
        )
    if result.unlocked_now:
        return Flash.ok(
            f"🎉 {hero_name} unlocked! ({result.shards_rolled} shards this summon)"
        )
    if result.jackpot:
        return Flash.info(
            f"🌟 Jackpot! {result.shards_rolled} shards "
            f"({result.new_total}/{SHARDS_PER_UNLOCK})"
        )
    return Flash.info(
        f"+{result.shards_rolled} shards ({result.new_total}/{SHARDS_PER_UNLOCK})"
    )


# -- rendering --------------------------------------------------------------


async def _render_embed(
    db: Database,
    user: discord.abc.User,
    hero_row,
    *,
    index: int | None = None,
    total: int | None = None,
    flash: Flash | None = None,
) -> discord.Embed:
    player = await db.get_or_create_player(user.id)
    count, pity = await _fetch_shards(db, user.id, hero_row["id"])
    owned = await _is_owned(db, user.id, hero_row["id"])

    color = RARITY_COLOR.get(hero_row["rarity"])
    rarity_emoji = RARITY_EMOJI.get(hero_row["rarity"], "•")
    position = (
        f"  ({index + 1}/{total})"
        if index is not None and total is not None
        else ""
    )
    embed = discord.Embed(
        title=f"🔮 {rarity_emoji} {hero_row['name']}{position}",
        color=color,
    )
    if hero_row["image_url"]:
        embed.set_thumbnail(url=hero_row["image_url"])
    apply_flash(embed, flash)

    embed.add_field(name="💎 Gems", value=f"{int(player['gems']):,}", inline=True)
    embed.add_field(
        name="Summon cost",
        value=f"{summon_cost(hero_row['rarity']):,}",
        inline=True,
    )
    embed.add_field(
        name="Rarity",
        value=hero_row["rarity"].capitalize(),
        inline=True,
    )

    if owned:
        progress = f"**Unlocked** · {count} shards banked"
    else:
        bar = _progress_bar(min(count, SHARDS_PER_UNLOCK), SHARDS_PER_UNLOCK)
        progress = (
            f"{bar}  {count}/{SHARDS_PER_UNLOCK}\n"
            f"You need {SHARDS_PER_UNLOCK} shards to unlock a hero."
        )
    embed.add_field(name="Unlock progress", value=progress, inline=False)

    embed.add_field(
        name="Pity",
        value=f"{pity}/{PITY_LIMIT} summons",
        inline=True,
    )
    return embed


def _progress_bar(value: int, total: int, width: int = 20) -> str:
    filled = min(width, max(0, round((value / total) * width)))
    return "█" * filled + "░" * (width - filled)


# -- view -------------------------------------------------------------------


class SummonView(discord.ui.View):
    def __init__(
        self,
        db: Database,
        owner_id: int,
        hero_ids: list[int],
        *,
        index: int = 0,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.hero_ids = hero_ids
        self.index = index % len(hero_ids) if hero_ids else 0

    @property
    def current_hero_id(self) -> int:
        return self.hero_ids[self.index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your summoning panel.", ephemeral=True
            )
            return False
        return True

    async def _refresh(
        self,
        interaction: discord.Interaction,
        flash: Flash | None = None,
    ) -> None:
        hero_row = await _fetch_hero_by_id(self.db, self.current_hero_id)
        if hero_row is None:
            # Hero deleted mid-flight — fall back to next index.
            self.hero_ids.pop(self.index)
            if not self.hero_ids:
                await interaction.response.edit_message(
                    content="The summon catalog is empty.", embed=None, view=None
                )
                return
            self.index %= len(self.hero_ids)
            hero_row = await _fetch_hero_by_id(self.db, self.current_hero_id)
        embed = await _render_embed(
            self.db,
            interaction.user,
            hero_row,
            index=self.index,
            total=len(self.hero_ids),
            flash=flash,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.index = (self.index - 1) % len(self.hero_ids)
        await self._refresh(interaction)

    @discord.ui.button(label="Summon", emoji="🔮", style=discord.ButtonStyle.success, row=0)
    async def summon_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        hero_row = await _fetch_hero_by_id(self.db, self.current_hero_id)
        if hero_row is None:
            await self._refresh(
                interaction, flash=Flash.err("That hero is gone.")
            )
            return

        cost = summon_cost(hero_row["rarity"])
        player = await self.db.get_or_create_player(interaction.user.id)
        if int(player["gems"]) < cost:
            await self._refresh(
                interaction,
                flash=Flash.err(
                    f"Not enough gems — need {cost:,} 💎, "
                    f"have {int(player['gems']):,}."
                ),
            )
            return

        _, flash = await execute_summon(self.db, interaction.user.id, hero_row)
        await self._refresh(interaction, flash=flash)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        self.index = (self.index + 1) % len(self.hero_ids)
        await self._refresh(interaction)

    @discord.ui.button(
        label="Level Up (100 shards)", emoji="📈",
        style=discord.ButtonStyle.primary, row=1,
    )
    async def levelup_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        flash = await spend_shards_for_levelup(
            self.db, interaction.user.id, self.current_hero_id,
        )
        await self._refresh(interaction, flash=flash)


# -- shard → level conversion ----------------------------------------------


async def spend_shards_for_levelup(
    db: Database, user_id: int, hero_id: int,
) -> Flash:
    """Consume 100 shards to bump a hero's level by the tier-appropriate
    amount (3 below lv50, 2 below 100, 1 at 100+). Clamped at the cap.
    """
    from wagame.game.hero_levels import (
        SHARDS_PER_LEVELUP,
        STARTING_LEVEL_CAP,
        levels_per_hundred_shards,
    )

    async with db.conn.execute(
        "SELECT level FROM owned_heroes "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        owned = await cur.fetchone()
    if owned is None:
        return Flash.err("You need to unlock the hero first (100 shards).")

    current_level = int(owned["level"])
    if current_level >= STARTING_LEVEL_CAP:
        return Flash.info(
            f"Hero is already at the level cap (Lv {STARTING_LEVEL_CAP})."
        )

    async with db.conn.execute(
        "SELECT count FROM hero_shards "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        shard_row = await cur.fetchone()
    shards = int(shard_row["count"]) if shard_row else 0
    if shards < SHARDS_PER_LEVELUP:
        need = SHARDS_PER_LEVELUP - shards
        return Flash.err(
            f"Need {need} more shards (have {shards}/{SHARDS_PER_LEVELUP})."
        )

    gain = levels_per_hundred_shards(current_level)
    new_level = min(STARTING_LEVEL_CAP, current_level + gain)
    actual_gain = new_level - current_level

    await db.conn.execute(
        "UPDATE hero_shards SET count = count - ? "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (SHARDS_PER_LEVELUP, user_id, hero_id),
    )
    await db.conn.execute(
        "UPDATE owned_heroes SET level = ? "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (new_level, user_id, hero_id),
    )
    await db.conn.commit()
    return Flash.ok(
        f"📈 +{actual_gain} levels → Lv {new_level}. "
        f"Spent {SHARDS_PER_LEVELUP} shards."
    )


# -- cog --------------------------------------------------------------------


class SummonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(
        name="summon",
        description="Open the summoning panel.",
    )
    @app_commands.describe(
        hero="Optional: jump straight to a specific hero (name or codename).",
    )
    async def summon(
        self,
        interaction: discord.Interaction,
        hero: str | None = None,
    ) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        hero_ids = await fetch_roster(self.db)
        if not hero_ids:
            from wagame.ui import Outcome, toast
            await interaction.response.send_message(
                embed=toast("Hero catalog is empty.", Outcome.ERROR),
                ephemeral=True,
            )
            return

        index = 0
        if hero:
            hit = await _fetch_hero_by_name(self.db, hero)
            if hit is not None and int(hit["id"]) in hero_ids:
                index = hero_ids.index(int(hit["id"]))

        view = SummonView(self.db, interaction.user.id, hero_ids, index=index)
        hero_row = await _fetch_hero_by_id(self.db, view.current_hero_id)
        embed = await _render_embed(
            self.db, interaction.user, hero_row,
            index=view.index, total=len(hero_ids),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @summon.autocomplete("hero")
    async def summon_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await _autocomplete_hero(self.db, current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SummonCog(bot))

