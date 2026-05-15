"""Witch's Council — weekly server-wide quest.

A single active quest at any time, global to the whole bot. Players
contribute through normal play (kills feed `kill_tenebrals` quests,
gather claims feed `gather_rss`, daily-quest claims feed
`complete_dailies`). The hourly loop spawns the next quest the moment
the current one settles, so there's always something to chase.

Each configured guild (`/admin set-council-channel`) gets a spawn
announcement and an end-of-quest payout announcement; the live
progress shows up in `/wa` and `/council`.
"""

from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from wagame.db import Database
from wagame.game.council import (
    COMPLETE_REWARD_GEMS,
    COMPLETE_REWARD_SHARDS,
    QUEST_DURATION_SECONDS,
    QuestKind,
    describe,
    is_completed,
    pick_kind,
    split_rewards,
    target_for,
    unit_label,
)
from wagame.ui import NEUTRAL_COLOR

log = logging.getLogger(__name__)

COUNCIL_TICK_MINUTES = 30  # how often the loop checks for spawn / settle


# -- DB helpers --------------------------------------------------------


async def active_quest(db: Database):
    """Return the row of the currently-running quest, or None."""
    async with db.conn.execute(
        "SELECT * FROM council_quests "
        "WHERE settled = 0 ORDER BY started_at DESC LIMIT 1"
    ) as cur:
        return await cur.fetchone()


async def _spawn_quest(
    db: Database,
    *,
    rng: random.Random | None = None,
    now: int | None = None,
    forced_kind: QuestKind | None = None,
) -> int:
    now = now if now is not None else int(time.time())
    kind = forced_kind if forced_kind is not None else pick_kind(rng)
    target = target_for(kind)
    async with db.conn.execute(
        """
        INSERT INTO council_quests
          (kind, target, progress, started_at, ends_at, settled, spawn_message_ids)
        VALUES (?, ?, 0, ?, ?, 0, '{}')
        """,
        (kind, target, now, now + QUEST_DURATION_SECONDS),
    ) as cur:
        quest_id = int(cur.lastrowid)
    await db.conn.commit()
    log.info("Council quest #%d spawned: %s (target %d)", quest_id, kind, target)
    return quest_id


async def record_contribution(
    db: Database,
    *,
    user_id: int,
    kind: QuestKind,
    amount: int,
) -> None:
    """Increment progress + per-user contribution if the active quest
    matches `kind`. No-op otherwise — keeps callers from caring whether a
    quest of the right kind is up.
    """
    if amount <= 0:
        return
    row = await active_quest(db)
    if row is None or row["kind"] != kind:
        return
    quest_id = int(row["id"])
    now = int(time.time())
    await db.conn.execute(
        "UPDATE council_quests SET progress = progress + ? WHERE id = ?",
        (amount, quest_id),
    )
    await db.conn.execute(
        """
        INSERT INTO council_contributions (quest_id, user_id, amount, last_contribution_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(quest_id, user_id) DO UPDATE SET
            amount = amount + excluded.amount,
            last_contribution_at = excluded.last_contribution_at
        """,
        (quest_id, user_id, amount, now),
    )
    await db.conn.commit()


async def _contributions(db: Database, quest_id: int):
    async with db.conn.execute(
        "SELECT user_id, amount FROM council_contributions "
        "WHERE quest_id = ? ORDER BY amount DESC",
        (quest_id,),
    ) as cur:
        return [(int(r["user_id"]), int(r["amount"])) for r in await cur.fetchall()]


async def _pick_random_unowned_hero(
    db: Database, user_id: int, rng: random.Random
) -> int | None:
    """Pick a random hero the user doesn't own — for shard rewards."""
    async with db.conn.execute(
        "SELECT id FROM heroes WHERE id NOT IN "
        "(SELECT hero_id FROM owned_heroes WHERE discord_user_id = ?)",
        (user_id,),
    ) as cur:
        candidates = [int(r["id"]) for r in await cur.fetchall()]
    if not candidates:
        return None
    return rng.choice(candidates)


async def _settle(
    bot: commands.Bot, db: Database, quest_row,
) -> None:
    """Mark settled, distribute rewards, post end announcements."""
    quest_id = int(quest_row["id"])
    target = int(quest_row["target"])
    progress = int(quest_row["progress"])
    completed = is_completed(progress, target)
    contributions = await _contributions(db, quest_id)
    awards = split_rewards(contributions, target, completed=completed)

    rng = random.Random(quest_id)
    paid: list[tuple[int, int, int]] = []
    from wagame.game.progression import grant_player_xp
    for award in awards:
        await db.conn.execute(
            "UPDATE players SET gems = gems + ? WHERE discord_user_id = ?",
            (award.gems, award.user_id),
        )
        shards_awarded = 0
        if award.shards > 0:
            hero_id = await _pick_random_unowned_hero(db, award.user_id, rng)
            if hero_id is not None:
                await db.conn.execute(
                    """
                    INSERT INTO hero_shards (discord_user_id, hero_id, count)
                    VALUES (?, ?, ?)
                    ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                        count = count + excluded.count
                    """,
                    (award.user_id, hero_id, award.shards),
                )
                shards_awarded = award.shards
        paid.append((award.user_id, award.gems, shards_awarded))
        # Player XP for qualifying contributors (big chunk for council).
        await grant_player_xp(db, award.user_id, 500)

    await db.conn.execute(
        "UPDATE council_quests SET settled = 1 WHERE id = ?", (quest_id,),
    )
    await db.conn.commit()

    embed = _end_embed(
        quest_row, completed=completed, paid=paid, bot=bot,
    )
    await _broadcast(bot, db, embed)


# -- announcements ----------------------------------------------------


async def _broadcast(bot: commands.Bot, db: Database, embed: discord.Embed) -> None:
    """Send `embed` to every configured council channel."""
    async with db.conn.execute(
        "SELECT channel_id FROM council_configs WHERE enabled = 1"
    ) as cur:
        rows = await cur.fetchall()
    for r in rows:
        channel_id = int(r["channel_id"])
        try:
            channel = (
                bot.get_channel(channel_id)
                or await bot.fetch_channel(channel_id)
            )
        except (discord.NotFound, discord.Forbidden):
            log.warning("council: cannot resolve channel %d", channel_id)
            continue
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("council: post failed in channel %d: %s", channel_id, exc)


def _spawn_embed(quest_row) -> discord.Embed:
    kind = quest_row["kind"]
    target = int(quest_row["target"])
    embed = discord.Embed(
        title="🏛 Witch's Council convenes",
        description=(
            f"A new collective quest is open. Together the server must "
            f"**{describe(kind).lower()}**.\n\n"
            f"**Target:** {target:,} {unit_label(kind)} by "
            f"<t:{int(quest_row['ends_at'])}:R>.\n"
            f"Every contributor (≥1% of target) earns gems and rare "
            f"hero shards on success."
        ),
        color=discord.Color.from_rgb(155, 89, 182),
    )
    embed.set_footer(text=f"Quest #{int(quest_row['id'])}")
    return embed


def _end_embed(
    quest_row,
    *,
    completed: bool,
    paid: list[tuple[int, int, int]],
    bot: commands.Bot,
) -> discord.Embed:
    kind = quest_row["kind"]
    target = int(quest_row["target"])
    progress = int(quest_row["progress"])
    pct = int(100 * progress / max(1, target))
    if completed:
        title = "🏆 Council quest cleared!"
        color = discord.Color.from_rgb(63, 185, 80)
        body = (
            f"The server hit **{progress:,} / {target:,}** "
            f"{unit_label(kind)} ({pct}%). Rewards inbound."
        )
    else:
        title = "⌛ Council quest missed"
        color = NEUTRAL_COLOR
        body = (
            f"Only **{progress:,} / {target:,}** {unit_label(kind)} "
            f"({pct}%). Consolation gems to top contributors."
        )

    embed = discord.Embed(title=title, description=body, color=color)
    if paid:
        lines = []
        for uid, gems, shards in paid[:10]:
            user = bot.get_user(uid)
            name = user.display_name if user else f"<@{uid}>"
            extras = f" · +{shards} shards" if shards else ""
            lines.append(f"• **{name}** — +{gems:,} 💎{extras}")
        embed.add_field(
            name="Payouts (top contributors)",
            value="\n".join(lines),
            inline=False,
        )
    return embed


# -- /council panel ---------------------------------------------------


async def _render_council_embed(
    db: Database, user: discord.abc.User
) -> discord.Embed:
    quest_row = await active_quest(db)
    if quest_row is None:
        return discord.Embed(
            title="🏛 Witch's Council",
            description="No quest is currently open. The Council is in recess.",
            color=NEUTRAL_COLOR,
        )
    kind = quest_row["kind"]
    target = int(quest_row["target"])
    progress = int(quest_row["progress"])
    pct = int(100 * progress / max(1, target))

    embed = discord.Embed(
        title=f"🏛 Council Quest: {describe(kind)}",
        color=discord.Color.from_rgb(155, 89, 182),
        description=(
            f"Server-wide target: **{progress:,} / {target:,}** "
            f"{unit_label(kind)} ({pct}%).\n"
            f"Ends <t:{int(quest_row['ends_at'])}:R>."
        ),
    )

    bar_width = 20
    fill = min(bar_width, max(0, int(bar_width * progress / max(1, target))))
    bar = "█" * fill + "░" * (bar_width - fill)
    embed.add_field(name="Progress", value=f"`{bar}` {pct}%", inline=False)

    # Top contributors.
    contributions = await _contributions(db, int(quest_row["id"]))
    if contributions:
        lines = []
        for i, (uid, amt) in enumerate(contributions[:10], start=1):
            lines.append(
                f"`{i:>2}.` <@{uid}> — {amt:,} {unit_label(kind)}"
            )
        embed.add_field(
            name="Top contributors", value="\n".join(lines), inline=False,
        )

    # Caller's own contribution.
    your_amount = next(
        (amt for uid, amt in contributions if uid == user.id), 0
    )
    embed.add_field(
        name="Your contribution",
        value=f"{your_amount:,} {unit_label(kind)}",
        inline=True,
    )
    embed.add_field(
        name="Rewards on completion",
        value=(
            f"+{COMPLETE_REWARD_GEMS} 💎 · +{COMPLETE_REWARD_SHARDS}x random "
            "hero shards"
        ),
        inline=True,
    )
    embed.set_footer(
        text=(
            "Contribute ≥1% of the target to qualify for rewards. "
            "Failed quests pay a consolation."
        )
    )
    return embed


class CouncilView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your council panel.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        embed = await _render_council_embed(self.db, interaction.user)
        await interaction.response.edit_message(embed=embed, view=self)


# -- cog --------------------------------------------------------------


class CouncilCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.council_tick.start()

    async def cog_unload(self) -> None:
        self.council_tick.cancel()

    @tasks.loop(minutes=COUNCIL_TICK_MINUTES)
    async def council_tick(self) -> None:
        try:
            await self._tick()
        except Exception:
            log.exception("council_tick failed")

    @council_tick.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _tick(self) -> None:
        now = int(time.time())
        row = await active_quest(self.db)
        if row is None:
            quest_id = await _spawn_quest(self.db)
            await self._announce_spawn(quest_id)
            return
        if now >= int(row["ends_at"]):
            await _settle(self.bot, self.db, row)
            new_id = await _spawn_quest(self.db)
            await self._announce_spawn(new_id)

    async def _announce_spawn(self, quest_id: int) -> None:
        async with self.db.conn.execute(
            "SELECT * FROM council_quests WHERE id = ?", (quest_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return
        embed = _spawn_embed(row)
        await _broadcast(self.bot, self.db, embed)

    @app_commands.command(
        name="council",
        description="View this week's Witch's Council quest.",
    )
    async def council(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        embed = await _render_council_embed(self.db, interaction.user)
        view = CouncilView(self.db, interaction.user.id)
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CouncilCog(bot))
