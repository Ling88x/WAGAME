"""Tenebral Sightings — random push events.

Background loop ticks every SIGHTING_TICK_MINUTES. For every active
player whose `next_sighting_at` has passed and who has no unclaimed
sighting in flight, the bot spawns one: picks a level in their hunt
reach, rolls reward bag, persists, and DMs the player with the news.

The player engages via `/sighting` (or the "Open Sighting" button in
`/wa`), which opens an ephemeral panel essentially identical to the
hunt panel — single Attack button, optional hero select, mob HP bar,
live expiry timestamp. Killing within the window awards 3x RSS, 3x
hero XP, and 1-5 random hero shards (current banked-shard hero if any,
otherwise a random unowned hero — keeps shards meaningful).

Persistence: a row in `tenebral_sightings` survives bot restarts. The
DM button (if added later) would not survive a restart, so the slash
command and hub entry are the canonical engagement surfaces.
"""

from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from wagame.db import Database
from wagame.game.hero_levels import (
    apply_xp_gain,
    atk_eff,
    command_pct,
    march_speed_pct,
)
from wagame.game.hunt import (
    apply_damage,
    get_tenebral,
    is_killed,
    march_power,
    min_power_for_level,
)
from wagame.game.sightings import (
    EXPIRY_SECONDS,
    build_spec,
    is_active_player,
    is_expired,
    pick_gap,
    roll_level,
)
from wagame.ui import NEUTRAL_COLOR, Flash, apply_flash

log = logging.getLogger(__name__)

SIGHTING_TICK_MINUTES = 10


# -- DB helpers ----------------------------------------------------------


async def _active_sighting(db: Database, user_id: int):
    async with db.conn.execute(
        "SELECT * FROM tenebral_sightings "
        "WHERE discord_user_id = ? AND claimed = 0 "
        "ORDER BY spawned_at DESC LIMIT 1",
        (user_id,),
    ) as cur:
        return await cur.fetchone()


async def _pick_shard_hero(
    db: Database, user_id: int, rng: random.Random
) -> int | None:
    """Prefer a hero the player has shard progress on; else any unowned hero."""
    async with db.conn.execute(
        "SELECT hero_id FROM hero_shards WHERE discord_user_id = ? AND count > 0",
        (user_id,),
    ) as cur:
        in_progress = [int(r["hero_id"]) for r in await cur.fetchall()]
    if in_progress:
        return rng.choice(in_progress)
    async with db.conn.execute(
        "SELECT id FROM heroes WHERE id NOT IN "
        "(SELECT hero_id FROM owned_heroes WHERE discord_user_id = ?)",
        (user_id,),
    ) as cur:
        candidates = [int(r["id"]) for r in await cur.fetchall()]
    return rng.choice(candidates) if candidates else None


async def spawn_sighting(
    db: Database,
    user_id: int,
    *,
    hunt_level_unlocked: int,
    rng: random.Random | None = None,
    now: int | None = None,
    forced_level: int | None = None,
) -> int:
    """Insert a sighting row + reschedule the player's next eligibility.

    Returns the new sighting id. Caller is responsible for delivering
    the DM (the cog's tasks loop does this; admin force-spawn shortcut
    can ignore the DM if it's spawning for self).
    """
    r = rng or random
    now = now if now is not None else int(time.time())
    level = forced_level if forced_level is not None else roll_level(
        hunt_level_unlocked, rng=r
    )
    spec = build_spec(level, rng=r)
    shard_hero_id = (
        await _pick_shard_hero(db, user_id, r) if spec.shard_count > 0 else None
    )

    async with db.conn.execute(
        """
        INSERT INTO tenebral_sightings
          (discord_user_id, level, hp_remaining, hp_max, spawned_at, expires_at,
           bonus_rss, bonus_xp, bonus_shard_hero_id, bonus_shard_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, level, spec.hp, spec.hp, now, now + EXPIRY_SECONDS,
            spec.bonus_rss, spec.bonus_xp, shard_hero_id, spec.shard_count,
        ),
    ) as cur:
        sighting_id = int(cur.lastrowid)

    next_at = now + pick_gap(rng=r)
    await db.conn.execute(
        "UPDATE players SET next_sighting_at = ? WHERE discord_user_id = ?",
        (next_at, user_id),
    )
    await db.conn.commit()
    return sighting_id


async def _engage_sighting(
    db: Database,
    user_id: int,
    sighting_id: int,
    *,
    hero_id: int,
    damage: int,
) -> tuple[Flash, dict]:
    async with db.conn.execute(
        "SELECT * FROM tenebral_sightings WHERE id = ?", (sighting_id,)
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return Flash.err("That sighting is gone."), {}
    if int(row["claimed"]) == 1:
        return Flash.err("Already resolved."), {}
    now = int(time.time())
    if is_expired(int(row["expires_at"]), now):
        # Mark claimed (with no rewards) so it falls out of the player's
        # active-sighting query and they can roll the next one.
        await db.conn.execute(
            "UPDATE tenebral_sightings SET claimed = 1 WHERE id = ?", (sighting_id,)
        )
        await db.conn.commit()
        return Flash.err("The sighting vanished — too late."), {"expired": True}

    spec = get_tenebral(int(row["level"]))
    new_hp = apply_damage(int(row["hp_remaining"]), damage)
    killed = is_killed(new_hp)

    summary: dict = {
        "level": int(row["level"]),
        "damage": damage,
        "killed": killed,
        "hp_before": int(row["hp_remaining"]),
        "hp_after": new_hp,
        "hp_max": int(row["hp_max"]),
        "tenebral_name": spec.name,
    }

    if killed:
        bonus_rss = int(row["bonus_rss"])
        share = bonus_rss // 3
        bonus_xp = int(row["bonus_xp"])
        await db.conn.execute(
            "UPDATE players SET gold = gold + ?, food = food + ?, wood = wood + ? "
            "WHERE discord_user_id = ?",
            (share, share, share, user_id),
        )
        # Hero XP cascade.
        async with db.conn.execute(
            "SELECT level, xp FROM owned_heroes "
            "WHERE discord_user_id = ? AND hero_id = ?",
            (user_id, hero_id),
        ) as cur:
            hero_row = await cur.fetchone()
        if hero_row is not None and bonus_xp > 0:
            result = apply_xp_gain(
                int(hero_row["level"]), int(hero_row["xp"]), bonus_xp
            )
            await db.conn.execute(
                "UPDATE owned_heroes SET level = ?, xp = ? "
                "WHERE discord_user_id = ? AND hero_id = ?",
                (result.new_level, result.new_xp, user_id, hero_id),
            )
            summary["hero_level_after"] = result.new_level
            summary["hero_levels_gained"] = result.levels_gained
        # Random hero shards.
        shard_hero_id = row["bonus_shard_hero_id"]
        shard_count = int(row["bonus_shard_count"])
        if shard_hero_id is not None and shard_count > 0:
            await db.conn.execute(
                """
                INSERT INTO hero_shards (discord_user_id, hero_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(discord_user_id, hero_id) DO UPDATE SET
                    count = count + excluded.count
                """,
                (user_id, int(shard_hero_id), shard_count),
            )
            summary["shard_hero_id"] = int(shard_hero_id)
            summary["shard_count"] = shard_count
        summary["bonus_rss"] = bonus_rss
        summary["bonus_xp"] = bonus_xp
        await db.conn.execute(
            "UPDATE tenebral_sightings SET hp_remaining = 0, claimed = 1 "
            "WHERE id = ?",
            (sighting_id,),
        )
    else:
        await db.conn.execute(
            "UPDATE tenebral_sightings SET hp_remaining = ? WHERE id = ?",
            (new_hp, sighting_id),
        )
    await db.conn.commit()

    # Bestiary: sighting attacks feed the same per-(kind, level) log as
    # regular hunts so the player's tenebral tally is unified.
    from wagame.cogs.bestiary import record_encounter
    await record_encounter(
        db,
        user_id=user_id,
        mob_kind="tenebral",
        mob_level=int(row["level"]),
        killed=killed,
    )

    # Council: sighting kills also tick the tenebral-slay quest.
    if killed:
        from wagame.cogs.council import record_contribution
        await record_contribution(
            db, user_id=user_id, kind="kill_tenebrals", amount=1,
        )

    if killed:
        bits = [f"Slain **{spec.name} (Lv{int(row['level'])})**!"]
        bits.append(f"+{summary['bonus_rss']:,} RSS (split).")
        if summary.get("shard_count"):
            bits.append(f"+{summary['shard_count']}x shards.")
        flash = Flash.ok(" ".join(bits))
    else:
        pct = int(100 * (new_hp / max(1, int(row["hp_max"]))))
        flash = Flash.info(
            f"{spec.name} (Lv{int(row['level'])}) took {damage:,} dmg — {pct}% HP left."
        )
    return flash, summary


# -- DM rendering --------------------------------------------------------


def _dm_embed(row, user: discord.abc.User) -> discord.Embed:
    spec = get_tenebral(int(row["level"]))
    embed = discord.Embed(
        title="🌙 Tenebral Sighting!",
        color=discord.Color.from_rgb(155, 89, 182),
        description=(
            f"A rare **{spec.name} (Lv{spec.level})** has surfaced — only you "
            f"can see it. Engage before <t:{int(row['expires_at'])}:R> or it "
            "vanishes."
        ),
    )
    embed.add_field(
        name="Reward on kill",
        value=(
            f"+{int(row['bonus_rss']):,} RSS · "
            f"+{int(row['bonus_xp']):,} hero XP · "
            f"+{int(row['bonus_shard_count'])}x shards"
        ),
        inline=False,
    )
    embed.add_field(
        name="How to engage",
        value="Use `/sighting` or the **Open Sighting** button in `/wa`.",
        inline=False,
    )
    embed.set_footer(text="Tenebral Sightings appear randomly. Don't miss them.")
    return embed


# -- panel rendering -----------------------------------------------------


async def _panel_embed(
    db: Database,
    user: discord.abc.User,
    sighting_row,
    selected_hero_id: int | None,
) -> discord.Embed:
    spec = get_tenebral(int(sighting_row["level"]))
    now = int(time.time())

    async with db.conn.execute(
        "SELECT * FROM players WHERE discord_user_id = ?", (user.id,)
    ) as cur:
        player = await cur.fetchone()

    troop_attack_pct = int(player["troop_attack_pct"])

    # Reuse hunt power computation by querying owned troops.
    async with db.conn.execute(
        """
        SELECT t.attack, o.count FROM owned_troops o
        JOIN troops t ON t.codename = o.troop_codename
        WHERE o.discord_user_id = ? AND o.count > 0
        """,
        (user.id,),
    ) as cur:
        troop_rows = await cur.fetchall()
    troops_atk = sum(int(r["attack"]) * int(r["count"]) for r in troop_rows)
    troops_total = sum(int(r["count"]) for r in troop_rows)

    hero_atk_value = 0
    hero_speed = 0
    hero_command = 0
    hero_name = None
    if selected_hero_id is not None:
        async with db.conn.execute(
            "SELECT h.name, h.rarity, o.level FROM owned_heroes o "
            "JOIN heroes h ON h.id = o.hero_id "
            "WHERE o.discord_user_id = ? AND h.id = ?",
            (user.id, selected_hero_id),
        ) as cur:
            hero_row = await cur.fetchone()
        if hero_row is not None:
            hero_name = hero_row["name"]
            hero_level = int(hero_row["level"])
            hero_atk_value = atk_eff(hero_row["rarity"], hero_level)
            hero_speed = march_speed_pct(hero_level)
            hero_command = command_pct(hero_level)

    from wagame.game.hunt import TroopStack
    troops = [
        TroopStack(
            codename=str(r["attack"]),  # codename not needed for math
            name="",
            tier=0,
            attack=int(r["attack"]),
            count=int(r["count"]),
        )
        for r in troop_rows
    ]
    power = march_power(
        hero_atk_value,
        troops,
        troop_attack_pct=troop_attack_pct,
        hero_command_pct=hero_command,
    )
    min_power = min_power_for_level(int(sighting_row["level"]))

    hp_left = int(sighting_row["hp_remaining"])
    hp_max = int(sighting_row["hp_max"])
    hp_bar_width = 14
    fill = min(hp_bar_width, max(0, int(hp_bar_width * hp_left / max(1, hp_max))))
    hp_strip = "█" * fill + "░" * (hp_bar_width - fill)

    embed = discord.Embed(
        title=f"🌙 Sighting: Lv{spec.level} {spec.name}",
        color=NEUTRAL_COLOR,
        description=(
            f"Engage before <t:{int(sighting_row['expires_at'])}:R> or the "
            "sighting vanishes."
        ),
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(
        name=f"🎯 HP {hp_left:,} / {hp_max:,}",
        value=f"`{hp_strip}`",
        inline=False,
    )
    embed.add_field(
        name=f"⚔️ March Power {power:,}",
        value=(
            f"Hero {hero_atk_value:,} + Troops {troops_atk:,} ({troops_total:,} units) "
            f"· min: {min_power:,}"
        ),
        inline=False,
    )
    hero_line = (
        f"**{hero_name}** · ⚔️ {hero_atk_value:,} · 🎖️ +{hero_command}% cmd · "
        f"🏇 +{hero_speed}%"
        if hero_name
        else "No hero selected — pick one below."
    )
    embed.add_field(name="🪄 Hero", value=hero_line, inline=False)
    embed.add_field(
        name="🎁 Bonus on kill",
        value=(
            f"+{int(sighting_row['bonus_rss']):,} RSS · "
            f"+{int(sighting_row['bonus_xp']):,} hero XP · "
            f"+{int(sighting_row['bonus_shard_count'])}x shards"
        ),
        inline=False,
    )
    if now >= int(sighting_row["expires_at"]):
        embed.set_footer(text="⌛ Sighting expired.")
    return embed


# -- hero selector (compact, mirrors hunt) -------------------------------


class _HeroSelect(discord.ui.Select):
    def __init__(self, owned, current: int | None) -> None:
        if not owned:
            super().__init__(
                placeholder="No heroes owned",
                options=[
                    discord.SelectOption(label="No heroes owned", value="none")
                ],
                row=1,
                disabled=True,
            )
            return
        options = [
            discord.SelectOption(
                label=f"{row['name']} · Lv {row['level']}",
                description=row["rarity"].capitalize(),
                value=str(int(row["id"])),
                default=(current is not None and int(row["id"]) == current),
            )
            for row in owned[:25]
        ]
        super().__init__(placeholder="Sighting hero", options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SightingView = self.view  # type: ignore[assignment]
        view.selected_hero_id = int(self.values[0])
        await view.refresh(interaction)


class SightingView(discord.ui.View):
    def __init__(
        self, db: Database, owner_id: int, sighting_id: int
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.sighting_id = sighting_id
        self.selected_hero_id: int | None = None

    async def initialize(self) -> None:
        owned = await _fetch_owned_heroes(self.db, self.owner_id)
        if owned:
            self.selected_hero_id = int(owned[0]["id"])
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        self.add_item(_HeroSelect(owned, self.selected_hero_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your sighting.", ephemeral=True
            )
            return False
        return True

    async def refresh(
        self, interaction: discord.Interaction, flash: Flash | None = None
    ) -> None:
        owned = await _fetch_owned_heroes(self.db, self.owner_id)
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        self.add_item(_HeroSelect(owned, self.selected_hero_id))

        row = await _active_sighting(self.db, self.owner_id)
        if row is None:
            embed = discord.Embed(
                title="🌙 No active sighting",
                description=(
                    "Wait for the next one to surface — they appear at random."
                ),
                color=NEUTRAL_COLOR,
            )
        else:
            embed = await _panel_embed(
                self.db, interaction.user, row, self.selected_hero_id
            )
        apply_flash(embed, flash)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Attack", emoji="⚔️", style=discord.ButtonStyle.danger, row=0)
    async def attack(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        row = await _active_sighting(self.db, self.owner_id)
        if row is None:
            await self.refresh(interaction, flash=Flash.err("Sighting gone."))
            return
        if self.selected_hero_id is None:
            await self.refresh(interaction, flash=Flash.err("Pick a hero first."))
            return

        async with self.db.conn.execute(
            "SELECT h.rarity, o.level FROM owned_heroes o "
            "JOIN heroes h ON h.id = o.hero_id "
            "WHERE o.discord_user_id = ? AND h.id = ?",
            (self.owner_id, self.selected_hero_id),
        ) as cur:
            hero_row = await cur.fetchone()
        if hero_row is None:
            await self.refresh(
                interaction, flash=Flash.err("That hero is no longer owned.")
            )
            return

        async with self.db.conn.execute(
            "SELECT * FROM players WHERE discord_user_id = ?", (self.owner_id,)
        ) as cur:
            player = await cur.fetchone()

        async with self.db.conn.execute(
            """
            SELECT t.attack, o.count FROM owned_troops o
            JOIN troops t ON t.codename = o.troop_codename
            WHERE o.discord_user_id = ? AND o.count > 0
            """,
            (self.owner_id,),
        ) as cur:
            troop_rows = await cur.fetchall()

        from wagame.game.hunt import TroopStack
        troops = [
            TroopStack(
                codename="", name="", tier=0,
                attack=int(r["attack"]), count=int(r["count"]),
            )
            for r in troop_rows
        ]
        hero_level = int(hero_row["level"])
        power = march_power(
            atk_eff(hero_row["rarity"], hero_level),
            troops,
            troop_attack_pct=int(player["troop_attack_pct"]),
            hero_command_pct=command_pct(hero_level),
        )
        min_power = min_power_for_level(int(row["level"]))
        if power < min_power:
            await self.refresh(
                interaction,
                flash=Flash.err(
                    f"March power {power:,} below the Lv{int(row['level'])} "
                    f"gate ({min_power:,})."
                ),
            )
            return

        flash, summary = await _engage_sighting(
            self.db,
            self.owner_id,
            self.sighting_id,
            hero_id=int(self.selected_hero_id),
            damage=power,
        )
        if summary.get("killed") and summary.get("hero_levels_gained"):
            flash = Flash.ok(
                f"{flash.message} Hero +{summary['hero_levels_gained']} lv "
                f"(now Lv{summary['hero_level_after']})."
            )

        # Diary DM hook — sighting kills get the same hunt_kill recap.
        if summary.get("killed"):
            from wagame.cogs.diary import try_send_diary
            await try_send_diary(
                interaction.client,  # type: ignore[arg-type]
                self.db,
                user_id=self.owner_id,
                hero_id=int(self.selected_hero_id),
                event="hunt_kill",
                context={
                    "mob": summary.get("tenebral_name", "Tenebral"),
                    "level": summary.get("level", 1),
                },
            )

        await self.refresh(interaction, flash=flash)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_btn(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.refresh(interaction)


async def _fetch_owned_heroes(db: Database, user_id: int):
    async with db.conn.execute(
        """
        SELECT h.id, h.name, h.rarity, o.level
        FROM owned_heroes o JOIN heroes h ON h.id = o.hero_id
        WHERE o.discord_user_id = ?
        ORDER BY
            CASE h.rarity
                WHEN 'mythic'    THEN 0
                WHEN 'legendary' THEN 1
                WHEN 'epic'      THEN 2
                WHEN 'rare'      THEN 3
                WHEN 'uncommon'  THEN 4
                WHEN 'common'    THEN 5
                ELSE 6
            END,
            o.level DESC,
            h.name
        """,
        (user_id,),
    ) as cur:
        return await cur.fetchall()


# -- background spawner --------------------------------------------------


class SightingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.sighting_tick.start()

    async def cog_unload(self) -> None:
        self.sighting_tick.cancel()

    @tasks.loop(minutes=SIGHTING_TICK_MINUTES)
    async def sighting_tick(self) -> None:
        try:
            await self._spawn_due_sightings()
        except Exception:
            log.exception("sighting_tick failed")

    @sighting_tick.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    async def dispatch_dm(
        bot: commands.Bot, db: Database, sighting_id: int
    ) -> bool:
        """Module-level entry — both the spawn loop and `/admin spawn-sighting`
        call this so a manually-spawned sighting still pings the player.
        Returns True if a DM was actually delivered.
        """
        async with db.conn.execute(
            "SELECT * FROM tenebral_sightings WHERE id = ?", (sighting_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            log.info("dispatch_dm: sighting %d not found", sighting_id)
            return False
        user_id = int(row["discord_user_id"])
        try:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        except discord.NotFound:
            log.warning("dispatch_dm: user %d not resolvable", user_id)
            return False
        embed = _dm_embed(row, user)
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            log.info(
                "dispatch_dm: user %d has DMs closed or blocks the bot", user_id
            )
            return False
        except discord.HTTPException as exc:
            log.warning("dispatch_dm: HTTP error sending DM to %d: %s", user_id, exc)
            return False
        log.info("dispatch_dm: sighting %d DM sent to %d", sighting_id, user_id)
        return True

    async def _spawn_due_sightings(self) -> None:
        now = int(time.time())
        async with self.db.conn.execute(
            """
            SELECT p.discord_user_id, p.last_seen
            FROM players p
            WHERE p.next_sighting_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM tenebral_sightings s
                WHERE s.discord_user_id = p.discord_user_id AND s.claimed = 0
              )
            """,
            (now,),
        ) as cur:
            candidates = await cur.fetchall()
        for row in candidates:
            user_id = int(row["discord_user_id"])
            last_seen_ts = _to_unix(row["last_seen"])
            if not is_active_player(last_seen_ts, now):
                # Push the next eligibility forward so we don't recheck
                # this idle account every tick.
                await self.db.conn.execute(
                    "UPDATE players SET next_sighting_at = ? "
                    "WHERE discord_user_id = ?",
                    (now + pick_gap(), user_id),
                )
                await self.db.conn.commit()
                continue
            # Until we expose a real "tenebral hunt level unlocked" stat,
            # cap the spawn band at lv4 — that's the comfortable range
            # for a casual account. Power gate filters reality on the panel.
            try:
                sighting_id = await spawn_sighting(
                    self.db,
                    user_id,
                    hunt_level_unlocked=3,
                    now=now,
                )
                await SightingsCog.dispatch_dm(self.bot, self.db, sighting_id)
            except Exception:
                log.exception("Failed to spawn sighting for user %d", user_id)

    @app_commands.command(
        name="sighting",
        description="Open your current Tenebral Sighting (if any).",
    )
    async def sighting(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        row = await _active_sighting(self.db, interaction.user.id)
        if row is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🌙 No active sighting",
                    description=(
                        "Wait for the next one — they appear at random "
                        "between 6 and 24 hours apart."
                    ),
                    color=NEUTRAL_COLOR,
                ),
                ephemeral=True,
            )
            return
        view = SightingView(self.db, interaction.user.id, int(row["id"]))
        await view.initialize()
        embed = await _panel_embed(
            self.db, interaction.user, row, view.selected_hero_id
        )
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )


def _to_unix(value) -> int:
    """`players.last_seen` is stored as `datetime('now')` ISO text; convert."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    # ISO 8601 like 'YYYY-MM-DD HH:MM:SS'
    try:
        import datetime as _dt
        dt = _dt.datetime.fromisoformat(str(value))
        return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp())
    except (TypeError, ValueError):
        return 0


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SightingsCog(bot))
