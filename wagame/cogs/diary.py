"""Hero Diary cog — DM dispatch + opt-out toggle.

Other cogs call `try_send_diary(bot, db, ...)` after resolving an event.
It checks the player's opt-in flag, the per-hero throttle, picks a line
from the corpus, and DMs the player in the hero's voice. Failures
(closed DMs, bot blocked, missing user) are swallowed so a single
broken inbox can't crash gameplay.

Players toggle diary DMs via `/settings diary` — also reachable from
the profile panel button (added in the same PR).
"""

from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.diary import (
    DIARY_THROTTLE_SECONDS,
    EventType,
    load_corpus,
    pick_line,
    should_send,
)

log = logging.getLogger(__name__)


# -- public entry point ---------------------------------------------------


async def try_send_diary(
    bot: commands.Bot,
    db: Database,
    *,
    user_id: int,
    hero_id: int | None,
    event: EventType,
    context: dict[str, str | int],
) -> bool:
    """Send a diary DM if the player opted in and the throttle allows it.

    Returns True if a DM was sent. Silent on every failure mode (DM
    closed, bot blocked, throttled, opted out, missing hero) — the
    caller's gameplay path shouldn't care.
    """
    if hero_id is None:
        return False

    # Opt-in check.
    async with db.conn.execute(
        "SELECT diary_dm_enabled FROM players WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        player_row = await cur.fetchone()
    if player_row is None or not int(player_row["diary_dm_enabled"]):
        return False

    # Throttle check.
    async with db.conn.execute(
        "SELECT h.name, h.codename, o.last_diary_at, o.level "
        "FROM owned_heroes o JOIN heroes h ON h.id = o.hero_id "
        "WHERE o.discord_user_id = ? AND o.hero_id = ?",
        (user_id, hero_id),
    ) as cur:
        hero_row = await cur.fetchone()
    if hero_row is None:
        return False

    now = int(time.time())
    if not should_send(int(hero_row["last_diary_at"]), now):
        return False

    # Compose.
    context_resolved: dict[str, str | int] = {
        "hero": hero_row["name"],
        "level": context.get("level", int(hero_row["level"])),
        "mob": context.get("mob", "—"),
        "rss": context.get("rss", "—"),
    }
    context_resolved.update(context)

    try:
        line = pick_line(event, context_resolved)
    except (KeyError, ValueError):
        log.exception("Diary template render failed for event %s", event)
        return False

    # Deliver.
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
    except discord.NotFound:
        return False
    try:
        message = f"**{hero_row['name']}**: {line}"
        await user.send(message)
    except (discord.Forbidden, discord.HTTPException):
        # DMs closed or rate-limited — silently move on; we'll try
        # again next event.
        return False

    await db.conn.execute(
        "UPDATE owned_heroes SET last_diary_at = ? "
        "WHERE discord_user_id = ? AND hero_id = ?",
        (now, user_id, hero_id),
    )
    await db.conn.commit()
    return True


# -- settings cog ---------------------------------------------------------


class SettingsCog(
    commands.GroupCog, group_name="settings", group_description="Personal toggles."
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    @app_commands.command(name="diary", description="Toggle Hero Diary DMs.")
    @app_commands.describe(state="on or off")
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    async def diary(
        self, interaction: discord.Interaction, state: app_commands.Choice[str]
    ) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        enabled = 1 if state.value == "on" else 0
        await self.db.conn.execute(
            "UPDATE players SET diary_dm_enabled = ? WHERE discord_user_id = ?",
            (enabled, interaction.user.id),
        )
        await self.db.conn.commit()
        verb = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"Hero Diary DMs **{verb}**. "
            f"Throttle: at most one DM per hero every "
            f"{DIARY_THROTTLE_SECONDS // 60} minutes.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    # Smoke-validate the corpus on cog load so a typo in the JSON
    # surfaces at boot, not on the first kill.
    load_corpus()
    await bot.add_cog(SettingsCog(bot))
