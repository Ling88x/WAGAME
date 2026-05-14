"""/hunt — tenebral hunting panel.

Single ephemeral panel: energy bar, level/hero selectors, Attack and
Claim-Daily buttons. State (selected level + hero) lives on the View
instance; the DB owns everything else.

Attack flow:
1. Validate (energy, march power, no active march).
2. Deduct energy, snapshot damage, insert hunt_marches row, edit panel
   to show the in-flight march.
3. asyncio.sleep(faux_seconds).
4. Resolve: apply damage to the spawn (auto-spawn if first hit on this
   level), bump daily kill quota on kill, award RSS + hero XP.
5. Final panel edit reflecting new state.

If the bot restarts mid-sleep, `cog_load` drains any unresolved march
rows by resolving them immediately so energy isn't lost.
"""

from __future__ import annotations

import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from wagame.db import Database
from wagame.game.daily import current_reset_day
from wagame.game.hero_levels import (
    apply_xp_gain,
    atk_eff,
    command_pct,
    march_speed_pct,
)
from wagame.game.hunt import (
    DAILY_QUOTA_KILLS,
    DAILY_QUOTA_REWARD_GEMS,
    DAILY_QUOTA_REWARD_RSS,
    ENERGY_CAP,
    TENEBRAL_TABLE,
    TroopStack,
    apply_damage,
    can_afford,
    faux_march_seconds,
    get_tenebral,
    is_killed,
    kill_reward,
    march_power,
    min_power_for_level,
    regen_energy,
)
from wagame.ui import NEUTRAL_COLOR, Flash, apply_flash

# -- DB helpers -----------------------------------------------------------


async def _regen_and_persist(db: Database, user_id: int) -> tuple[int, int]:
    """Bring the player's energy up to date and persist. Returns (energy, updated_at)."""
    async with db.conn.execute(
        "SELECT energy, energy_updated_at FROM players WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    now = int(time.time())
    energy, updated_at = regen_energy(int(row["energy"]), int(row["energy_updated_at"]), now)
    if (energy, updated_at) != (int(row["energy"]), int(row["energy_updated_at"])):
        await db.conn.execute(
            "UPDATE players SET energy = ?, energy_updated_at = ? WHERE discord_user_id = ?",
            (energy, updated_at, user_id),
        )
        await db.conn.commit()
    return energy, updated_at


async def _spend_energy(db: Database, user_id: int, cost: int) -> bool:
    """Atomically deduct `cost` energy. Returns True on success."""
    energy, _ = await _regen_and_persist(db, user_id)
    if not can_afford(energy, cost):
        return False
    await db.conn.execute(
        "UPDATE players SET energy = energy - ? WHERE discord_user_id = ?",
        (cost, user_id),
    )
    await db.conn.commit()
    return True


async def _active_march(db: Database, user_id: int):
    async with db.conn.execute(
        "SELECT * FROM hunt_marches WHERE discord_user_id = ? AND resolved = 0",
        (user_id,),
    ) as cur:
        return await cur.fetchone()


async def _fetch_player(db: Database, user_id: int):
    async with db.conn.execute(
        "SELECT * FROM players WHERE discord_user_id = ?", (user_id,)
    ) as cur:
        return await cur.fetchone()


async def _fetch_troops(db: Database, user_id: int) -> list[TroopStack]:
    """Auto-fill: every owned troop joins the march. No selection UI in v1."""
    async with db.conn.execute(
        """
        SELECT t.codename, t.name, t.tier, t.attack, o.count
        FROM owned_troops o
        JOIN troops t ON t.codename = o.troop_codename
        WHERE o.discord_user_id = ? AND o.count > 0
        ORDER BY t.tier DESC, t.name
        """,
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        TroopStack(
            codename=r["codename"],
            name=r["name"],
            tier=int(r["tier"]),
            attack=int(r["attack"]),
            count=int(r["count"]),
        )
        for r in rows
    ]


async def _fetch_owned_heroes(db: Database, user_id: int):
    async with db.conn.execute(
        """
        SELECT h.id, h.name, h.rarity, o.level, o.xp
        FROM owned_heroes o
        JOIN heroes h ON h.id = o.hero_id
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


async def _hero_by_id(db: Database, user_id: int, hero_id: int):
    async with db.conn.execute(
        """
        SELECT h.id, h.name, h.rarity, o.level, o.xp
        FROM owned_heroes o
        JOIN heroes h ON h.id = o.hero_id
        WHERE o.discord_user_id = ? AND h.id = ?
        """,
        (user_id, hero_id),
    ) as cur:
        return await cur.fetchone()


async def _spawn_or_get(db: Database, user_id: int, level: int):
    async with db.conn.execute(
        "SELECT * FROM tenebral_spawns WHERE discord_user_id = ? AND level = ?",
        (user_id, level),
    ) as cur:
        row = await cur.fetchone()
    if row is not None:
        return row
    spec = get_tenebral(level)
    now = int(time.time())
    await db.conn.execute(
        """
        INSERT INTO tenebral_spawns
          (discord_user_id, level, hp_remaining, hp_max, spawned_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, level, spec.hp, spec.hp, now),
    )
    await db.conn.commit()
    async with db.conn.execute(
        "SELECT * FROM tenebral_spawns WHERE discord_user_id = ? AND level = ?",
        (user_id, level),
    ) as cur:
        return await cur.fetchone()


async def _daily_row(db: Database, user_id: int):
    label = current_reset_day().isoformat()
    await db.conn.execute(
        "INSERT OR IGNORE INTO hunt_daily_progress (discord_user_id, reset_day) "
        "VALUES (?, ?)",
        (user_id, label),
    )
    await db.conn.commit()
    async with db.conn.execute(
        "SELECT * FROM hunt_daily_progress WHERE discord_user_id = ? AND reset_day = ?",
        (user_id, label),
    ) as cur:
        return await cur.fetchone()


async def _bump_daily(db: Database, user_id: int) -> int:
    label = current_reset_day().isoformat()
    await db.conn.execute(
        "INSERT OR IGNORE INTO hunt_daily_progress (discord_user_id, reset_day) "
        "VALUES (?, ?)",
        (user_id, label),
    )
    await db.conn.execute(
        "UPDATE hunt_daily_progress SET kills = kills + 1 "
        "WHERE discord_user_id = ? AND reset_day = ?",
        (user_id, label),
    )
    await db.conn.commit()
    async with db.conn.execute(
        "SELECT kills FROM hunt_daily_progress WHERE discord_user_id = ? AND reset_day = ?",
        (user_id, label),
    ) as cur:
        row = await cur.fetchone()
    return int(row["kills"])


async def _engage_march(db: Database, march_id: int) -> tuple[Flash, dict]:
    """Outbound leg complete: apply damage, award rewards if killed, set engaged_at.

    Does NOT mark `resolved`; the return leg still has to run before the
    march counts as finalised. Idempotent against re-entry — if engaged_at
    is already populated, returns a no-op flash without re-applying damage.
    """
    async with db.conn.execute(
        "SELECT * FROM hunt_marches WHERE id = ?", (march_id,)
    ) as cur:
        march = await cur.fetchone()
    if march is None:
        return Flash.err("March not found."), {}
    if march["engaged_at"] is not None:
        return Flash.info("Already engaged."), {}

    user_id = int(march["discord_user_id"])
    level = int(march["level"])
    damage = int(march["damage"])
    hero_id = march["hero_id"]

    spawn = await _spawn_or_get(db, user_id, level)
    spec = get_tenebral(level)
    new_hp = apply_damage(int(spawn["hp_remaining"]), damage)
    killed = is_killed(new_hp)

    summary: dict = {
        "level": level,
        "damage": damage,
        "killed": killed,
        "hp_before": int(spawn["hp_remaining"]),
        "hp_after": new_hp,
        "hp_max": int(spawn["hp_max"]),
        "tenebral_name": spec.name,
    }

    if killed:
        reward = kill_reward(level)
        summary["reward"] = reward
        await db.conn.execute(
            "DELETE FROM tenebral_spawns WHERE discord_user_id = ? AND level = ?",
            (user_id, level),
        )
        await db.conn.execute(
            "UPDATE players SET gold = gold + ?, food = food + ?, wood = wood + ? "
            "WHERE discord_user_id = ?",
            (reward.gold, reward.food, reward.wood, user_id),
        )
        if hero_id is not None:
            async with db.conn.execute(
                "SELECT level, xp FROM owned_heroes "
                "WHERE discord_user_id = ? AND hero_id = ?",
                (user_id, hero_id),
            ) as cur:
                hero_row = await cur.fetchone()
            if hero_row is not None:
                lvl_result = apply_xp_gain(
                    int(hero_row["level"]), int(hero_row["xp"]), reward.xp
                )
                await db.conn.execute(
                    "UPDATE owned_heroes SET level = ?, xp = ? "
                    "WHERE discord_user_id = ? AND hero_id = ?",
                    (lvl_result.new_level, lvl_result.new_xp, user_id, hero_id),
                )
                summary["hero_level_after"] = lvl_result.new_level
                summary["hero_levels_gained"] = lvl_result.levels_gained
        kills_today = await _bump_daily(db, user_id)
        summary["kills_today"] = kills_today
    else:
        await db.conn.execute(
            "UPDATE tenebral_spawns SET hp_remaining = ? "
            "WHERE discord_user_id = ? AND level = ?",
            (new_hp, user_id, level),
        )

    await db.conn.execute(
        "UPDATE hunt_marches SET engaged_at = ? WHERE id = ?",
        (int(time.time()), march_id),
    )
    await db.conn.commit()

    if killed:
        flash = Flash.ok(
            f"Slain **{spec.name} (Lv{level})**! +{damage:,} dmg final blow."
        )
    else:
        pct = int(100 * (new_hp / max(1, int(spawn["hp_max"]))))
        flash = Flash.info(
            f"{spec.name} (Lv{level}) took {damage:,} dmg — {pct}% HP left."
        )
    return flash, summary


async def _finalize_march(db: Database, march_id: int) -> None:
    """Return leg complete: mark resolved. Hero is now free for the next march."""
    await db.conn.execute(
        "UPDATE hunt_marches SET resolved = 1 WHERE id = ?", (march_id,)
    )
    await db.conn.commit()


async def _resolve_march(db: Database, march_id: int) -> tuple[Flash, dict]:
    """One-shot: engage + finalize. Used by cog_load drain and DB-level tests."""
    flash, summary = await _engage_march(db, march_id)
    await _finalize_march(db, march_id)
    return flash, summary


async def _claim_daily(db: Database, user_id: int) -> tuple[bool, Flash]:
    row = await _daily_row(db, user_id)
    if int(row["kills"]) < DAILY_QUOTA_KILLS:
        need = DAILY_QUOTA_KILLS - int(row["kills"])
        return False, Flash.err(f"Need {need} more kill(s) before claiming.")
    if int(row["reward_claimed"]) == 1:
        return False, Flash.info("Daily reward already claimed.")
    rss = DAILY_QUOTA_REWARD_RSS
    await db.conn.execute(
        "UPDATE players SET gold = gold + ?, food = food + ?, wood = wood + ?, "
        "gems = gems + ? WHERE discord_user_id = ?",
        (
            rss["gold"], rss["food"], rss["wood"],
            DAILY_QUOTA_REWARD_GEMS, user_id,
        ),
    )
    await db.conn.execute(
        "UPDATE hunt_daily_progress SET reward_claimed = 1 "
        "WHERE discord_user_id = ? AND reset_day = ?",
        (user_id, current_reset_day().isoformat()),
    )
    await db.conn.commit()
    return True, Flash.ok(
        f"Daily claimed: +{DAILY_QUOTA_REWARD_GEMS} gems, "
        f"+{rss['gold']:,}/{rss['food']:,}/{rss['wood']:,} gold/food/wood."
    )


# -- rendering ------------------------------------------------------------


def _energy_bar(current: int, cap: int, width: int = 14) -> str:
    fill = min(width, max(0, int(width * current / max(1, cap))))
    return "█" * fill + "░" * (width - fill)


async def _render_embed(
    db: Database,
    user: discord.abc.User,
    selected_level: int,
    selected_hero_id: int | None,
) -> discord.Embed:
    player = await _fetch_player(db, user.id)
    energy = int(player["energy"])
    troop_attack_pct = int(player["troop_attack_pct"])

    troops = await _fetch_troops(db, user.id)
    hero_row = (
        await _hero_by_id(db, user.id, selected_hero_id)
        if selected_hero_id is not None
        else None
    )

    hero_atk_value = 0
    hero_speed = 0
    hero_command = 0
    if hero_row is not None:
        hero_level = int(hero_row["level"])
        hero_atk_value = atk_eff(hero_row["rarity"], hero_level)
        hero_speed = march_speed_pct(hero_level)
        hero_command = command_pct(hero_level)
    power = march_power(
        hero_atk_value,
        troops,
        troop_attack_pct=troop_attack_pct,
        hero_command_pct=hero_command,
    )

    spec = get_tenebral(selected_level)
    spawn = None
    async with db.conn.execute(
        "SELECT * FROM tenebral_spawns WHERE discord_user_id = ? AND level = ?",
        (user.id, selected_level),
    ) as cur:
        spawn = await cur.fetchone()
    hp_left = int(spawn["hp_remaining"]) if spawn else spec.hp
    hp_max = int(spawn["hp_max"]) if spawn else spec.hp
    min_power = min_power_for_level(selected_level)

    daily = await _daily_row(db, user.id)
    kills_today = int(daily["kills"])
    claimed = bool(daily["reward_claimed"])

    embed = discord.Embed(
        title="🦌 Tenebral Hunt",
        color=NEUTRAL_COLOR,
    )
    embed.set_thumbnail(url=user.display_avatar.url)

    embed.add_field(
        name=f"⚡ Energy {energy}/{ENERGY_CAP}",
        value=f"`{_energy_bar(energy, ENERGY_CAP)}`",
        inline=False,
    )

    troops_total = sum(t.count for t in troops)
    troops_atk_sum = sum(t.attack_contribution for t in troops)
    base = hero_atk_value + troops_atk_sum
    mults: list[str] = []
    if hero_command:
        mults.append(f"command x{1 + hero_command / 100:.2f}")
    if troop_attack_pct:
        mults.append(f"research x{1 + troop_attack_pct / 100:.2f}")
    mults_str = f" -> {' x '.join(mults)}" if mults else ""
    embed.add_field(
        name=f"⚔️ March Power {power:,}",
        value=(
            f"Hero {hero_atk_value:,} + Troops {troops_atk_sum:,} "
            f"({troops_total:,} units) = {base:,}{mults_str}"
        ),
        inline=False,
    )

    hp_bar_width = 14
    hp_fill = (
        min(hp_bar_width, max(0, int(hp_bar_width * hp_left / max(1, hp_max))))
    )
    hp_strip = "█" * hp_fill + "░" * (hp_bar_width - hp_fill)
    embed.add_field(
        name=f"🎯 Target: Lv{spec.level} {spec.name}",
        value=(
            f"HP: {hp_left:,} / {hp_max:,}\n"
            f"`{hp_strip}`\n"
            f"Energy cost: {spec.energy_cost} · Min power: {min_power:,}"
        ),
        inline=False,
    )

    if hero_row is None:
        hero_line = "**No hero selected** — pick one below."
    else:
        bits = [
            f"**{hero_row['name']}**",
            f"Lv {hero_row['level']}",
            f"⚔️ {hero_atk_value:,}",
        ]
        if hero_command:
            bits.append(f"🎖️ +{hero_command}% cmd")
        if hero_speed:
            bits.append(f"🏇 +{hero_speed}%")
        hero_line = " · ".join(bits)
    embed.add_field(name="🪄 Hero", value=hero_line, inline=False)

    quota_state = (
        "✓ claimed" if claimed
        else ("ready to claim" if kills_today >= DAILY_QUOTA_KILLS else "in progress")
    )
    embed.add_field(
        name=f"📅 Daily Quota — {kills_today}/{DAILY_QUOTA_KILLS}",
        value=f"{quota_state}\n"
              f"Reward: +{DAILY_QUOTA_REWARD_GEMS} gems · "
              f"+{DAILY_QUOTA_REWARD_RSS['gold']:,} gold/food/wood",
        inline=False,
    )

    # In-flight march, surfaced from DB so Refresh never erases it.
    active = await _active_march(db, user.id)
    if active is not None:
        active_spec = get_tenebral(int(active["level"]))
        started = int(active["started_at"])
        completes = int(active["completes_at"])
        midpoint = started + (completes - started) // 2
        engaged_at = active["engaged_at"]
        now = int(time.time())
        if engaged_at is None:
            # Outbound leg.
            label = f"🏇 Marching to Lv{active_spec.level} {active_spec.name}"
            if now >= midpoint:
                # Resolver hasn't fired yet — show "engaging now".
                value = "Engaging now…"
            else:
                value = (
                    f"Engages <t:{midpoint}:R> · returns <t:{completes}:R>\n"
                    f"Locked damage: {int(active['damage']):,}"
                )
        else:
            # Return leg.
            label = f"🛡️ Returning from Lv{active_spec.level} {active_spec.name}"
            value = (
                "Arriving now…"
                if now >= completes
                else f"Hero back home <t:{completes}:R>"
            )
        embed.add_field(name=label, value=value, inline=False)

    return embed


# -- view -----------------------------------------------------------------


class LevelSelect(discord.ui.Select):
    def __init__(self, current: int) -> None:
        options = [
            discord.SelectOption(
                label=f"Lv{t.level} · {t.name}",
                description=f"HP {t.hp:,} · {t.energy_cost}⚡",
                value=str(t.level),
                default=(t.level == current),
            )
            for t in TENEBRAL_TABLE
        ]
        super().__init__(placeholder="Target level", options=options, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HuntView = self.view  # type: ignore[assignment]
        view.selected_level = int(self.values[0])
        # Re-render the select so the new default sticks visually.
        view._rebuild_selects()
        await view.refresh(interaction)


class HeroSelect(discord.ui.Select):
    def __init__(self, owned, current: int | None) -> None:
        if not owned:
            options = [
                discord.SelectOption(
                    label="No heroes owned",
                    description="Try /summon to recruit one.",
                    value="none",
                )
            ]
            super().__init__(
                placeholder="No heroes owned", options=options, row=3, disabled=True
            )
            return

        options = []
        for row in owned[:25]:
            options.append(
                discord.SelectOption(
                    label=f"{row['name']} · Lv {row['level']}",
                    description=row["rarity"].capitalize(),
                    value=str(int(row["id"])),
                    default=(current is not None and int(row["id"]) == current),
                )
            )
        super().__init__(placeholder="March hero", options=options, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HuntView = self.view  # type: ignore[assignment]
        view.selected_hero_id = int(self.values[0])
        view._rebuild_selects()
        await view.refresh(interaction)


class HuntView(discord.ui.View):
    def __init__(self, db: Database, owner_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.db = db
        self.owner_id = owner_id
        self.selected_level: int = 1
        self.selected_hero_id: int | None = None
        self._level_select: LevelSelect | None = None
        self._hero_select: HeroSelect | None = None

    async def initialize(self) -> None:
        """Pick sensible default hero (highest-rarity, highest-level owned)."""
        owned = await _fetch_owned_heroes(self.db, self.owner_id)
        if owned:
            self.selected_hero_id = int(owned[0]["id"])
        await self._build(owned)

    async def _build(self, owned=None) -> None:
        if owned is None:
            owned = await _fetch_owned_heroes(self.db, self.owner_id)
        # Remove any existing selects so we don't double them on refresh.
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        self._level_select = LevelSelect(self.selected_level)
        self._hero_select = HeroSelect(owned, self.selected_hero_id)
        self.add_item(self._level_select)
        self.add_item(self._hero_select)

    def _rebuild_selects(self) -> None:
        # Sync select defaults; called when level/hero changes via dropdown.
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        self._level_select = LevelSelect(self.selected_level)
        self.add_item(self._level_select)
        # Hero list doesn't change between refreshes within a session; we'd
        # rebuild from DB to catch admin grants, but the inflight callback
        # path doesn't hit that case — keep it lazy.

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "This isn't your hunt panel.", ephemeral=True
            )
            return False
        return True

    async def refresh(
        self,
        interaction: discord.Interaction,
        flash: Flash | None = None,
    ) -> None:
        # Re-fetch heroes so admin grants mid-session surface.
        owned = await _fetch_owned_heroes(self.db, self.owner_id)
        if (
            self.selected_hero_id is not None
            and not any(int(r["id"]) == self.selected_hero_id for r in owned)
        ):
            self.selected_hero_id = int(owned[0]["id"]) if owned else None
        await self._build(owned)
        embed = await _render_embed(
            self.db, interaction.user, self.selected_level, self.selected_hero_id
        )
        apply_flash(embed, flash)
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Attack", emoji="⚔️", style=discord.ButtonStyle.danger, row=0)
    async def attack(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        # Snapshot state before doing async work; users can fire double-clicks.
        level = self.selected_level
        hero_id = self.selected_hero_id

        if hero_id is None:
            await self.refresh(
                interaction, flash=Flash.err("Pick a hero from the dropdown first.")
            )
            return

        existing = await _active_march(self.db, interaction.user.id)
        if existing is not None:
            await self.refresh(interaction, flash=Flash.err("A march is already in flight."))
            return

        spec = get_tenebral(level)
        hero_row = await _hero_by_id(self.db, interaction.user.id, hero_id)
        if hero_row is None:
            await self.refresh(
                interaction, flash=Flash.err("That hero is no longer owned."),
            )
            return

        troops = await _fetch_troops(self.db, interaction.user.id)
        player = await _fetch_player(self.db, interaction.user.id)
        hero_level = int(hero_row["level"])
        hero_atk_value = atk_eff(hero_row["rarity"], hero_level)
        hero_speed = march_speed_pct(hero_level)
        hero_command = command_pct(hero_level)
        power = march_power(
            hero_atk_value,
            troops,
            troop_attack_pct=int(player["troop_attack_pct"]),
            hero_command_pct=hero_command,
        )
        min_power = min_power_for_level(level)
        if power < min_power:
            await self.refresh(
                interaction,
                flash=Flash.err(
                    f"March power {power:,} below the Lv{level} gate ({min_power:,})."
                ),
            )
            return

        if not await _spend_energy(self.db, interaction.user.id, spec.energy_cost):
            await self.refresh(
                interaction,
                flash=Flash.err(
                    f"Not enough energy — need {spec.energy_cost}⚡."
                ),
            )
            return

        leg_seconds = faux_march_seconds(level, hero_march_speed_pct=hero_speed)
        now = int(time.time())
        completes_at = now + 2 * leg_seconds
        async with self.db.conn.execute(
            """
            INSERT INTO hunt_marches
              (discord_user_id, level, hero_id, started_at, completes_at, damage)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (interaction.user.id, level, hero_id, now, completes_at, power),
        ) as cur:
            march_id = cur.lastrowid
        await self.db.conn.commit()

        # Defer so we can sleep across the round trip and edit in-place.
        await interaction.response.defer()
        await self.refresh(interaction)  # picks up the outbound row from DB

        # Outbound leg: hero travels to the mob.
        await asyncio.sleep(leg_seconds)
        engage_flash, summary = await _engage_march(self.db, int(march_id))
        if summary.get("killed") and summary.get("hero_levels_gained"):
            engage_flash = Flash.ok(
                f"{engage_flash.message} Hero +{summary['hero_levels_gained']} lv "
                f"(now Lv{summary['hero_level_after']})."
            )
        await self.refresh(interaction, flash=engage_flash)

        # Return leg: hero rides home; rewards already credited at engagement.
        await asyncio.sleep(leg_seconds)
        await _finalize_march(self.db, int(march_id))
        await self.refresh(interaction)

    @discord.ui.button(label="Claim Daily", emoji="🎁", style=discord.ButtonStyle.success, row=0)
    async def claim_daily(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        _, flash = await _claim_daily(self.db, interaction.user.id)
        await self.refresh(interaction, flash=flash)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await self.refresh(interaction)


# -- cog ------------------------------------------------------------------


class HuntCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Database:
        return self.bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        # Drain any in-flight marches abandoned by a prior boot: resolve
        # them silently so the energy spent earlier still pays off.
        async with self.db.conn.execute(
            "SELECT id FROM hunt_marches WHERE resolved = 0"
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            await _resolve_march(self.db, int(row["id"]))

    @app_commands.command(name="hunt", description="Open your tenebral hunting panel.")
    async def hunt(self, interaction: discord.Interaction) -> None:
        await self.db.get_or_create_player(interaction.user.id)
        await _regen_and_persist(self.db, interaction.user.id)
        view = HuntView(self.db, interaction.user.id)
        await view.initialize()
        embed = await _render_embed(
            self.db, interaction.user, view.selected_level, view.selected_hero_id
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HuntCog(bot))
