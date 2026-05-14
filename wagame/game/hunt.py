"""Pure logic for Tenebral Hunt — no Discord, no DB.

Numbers here are the in-game ground truth from docs/tenebral-data.md. The
HP curve is brutal on purpose ("ciężko uderzyć każdy następny poziom"):
default players one-shot lv1, grind for lv2-3, and lv6+ stays out of
reach until research and hero levels stack up.

Energy
------
- Cap defaults to 500. Regenerates 1 point every 30 seconds via a lazy
  formula keyed on `players.energy_updated_at` — no tick task.
- `regen(stored, updated_at, now, cap)` returns the new
  `(energy, updated_at)` pair without ever exceeding `cap`. Excess
  elapsed time that doesn't compound into a full regen tick stays
  unbanked (updated_at advances by `gained * regen_seconds` only).

March power
-----------
march_power = (hero_eff_atk + sum(owned_troop.atk * count))
              * (1 + research.troop_attack_pct / 100)

Hero contribution is multiplied through the troop_attack_pct buff too —
the same buff that boosts troop ATK also amplifies a commander hero,
which mirrors how the in-game player attacker tactics bonus stacks.

Damage roll
-----------
v1 is deterministic: damage applied to the mob equals march_power. No
crits, no variance. Variance lands in a balance pass once the loop feels
shippable.

Faux-timer
----------
Each tenebral level has an in-game base march time. We scale it down by
the hero march-speed bonus (hero_levels.march_speed_pct) and the
research march-speed bonus (placeholder column not yet wired). Hard
clamp to [MIN_MARCH_SECONDS, MAX_MARCH_SECONDS] so the click->result
loop doesn't feel either instant or punishing.
"""

from __future__ import annotations

from dataclasses import dataclass

# Energy pool defaults — cap and regen are constants in v1. Future
# research nodes ("Tenebral Hunt Level", "Energy Regen Bonus") will lift
# them via player columns.
ENERGY_CAP = 500
ENERGY_REGEN_SECONDS = 30

# Faux-timer clamp. Real in-game marches range ~2-8 minutes; we squash
# them to a snappier Discord rhythm.
MIN_MARCH_SECONDS = 15
MAX_MARCH_SECONDS = 90

# Daily quota — 10 kills/day, claimable once per reset day.
DAILY_QUOTA_KILLS = 10
DAILY_QUOTA_REWARD_GEMS = 50
DAILY_QUOTA_REWARD_RSS = {"gold": 100_000, "food": 100_000, "wood": 100_000}


@dataclass(frozen=True)
class Tenebral:
    level: int
    name: str
    hp: int
    energy_cost: int
    base_march_seconds: int  # in-game baseline, pre-hero/research scaling
    reward_xp: int           # awarded to the march hero on kill
    reward_rss: int          # split across gold/food/wood evenly


# In-game HP curve copied 1:1 from docs/tenebral-data.md. Energy cost
# matches the in-game "Energy" column. Base march seconds are squashed
# from the in-game minutes-long marches; faux-timer clamps still apply.
# Reward XP scales linearly with level; reward_rss roughly tracks HP
# (kill effort).
TENEBRAL_TABLE: tuple[Tenebral, ...] = (
    Tenebral(1,  "Void Cervus",  750,             25,  60, 100,    500),
    Tenebral(2,  "Void Vespa",   30_000,          35,  55, 200,   2_000),
    Tenebral(3,  "Void Lupus",   450_000,         65,  50, 300,  10_000),
    Tenebral(4,  "Void Cervus",  3_600_000,       67,  48, 400,  40_000),
    Tenebral(5,  "Void Cervus",  25_000_000,      73,  45, 500, 150_000),
    Tenebral(6,  "Void Lupus",   88_000_000,      79,  42, 600, 400_000),
    Tenebral(7,  "Void Lupus",   275_000_000,     88,  40, 700, 1_000_000),
    Tenebral(8,  "Void Vespa",   900_000_000,     92,  38, 800, 2_500_000),
    Tenebral(9,  "Void Lupus",   4_000_000_000,   97,  36, 900, 6_000_000),
    Tenebral(10, "Void Lupus",   13_000_000_000, 125,  35, 1000, 15_000_000),
    Tenebral(11, "Void Cervus",  40_000_000_000, 132,  34, 1100, 35_000_000),
    Tenebral(12, "Void Cervus", 100_000_000_000, 139,  32, 1200, 80_000_000),
)

# Convenience: dict keyed by level.
TENEBRAL_BY_LEVEL: dict[int, Tenebral] = {t.level: t for t in TENEBRAL_TABLE}


def get_tenebral(level: int) -> Tenebral:
    if level not in TENEBRAL_BY_LEVEL:
        raise ValueError(f"Unknown tenebral level: {level}")
    return TENEBRAL_BY_LEVEL[level]


# -- energy ---------------------------------------------------------------


def regen_energy(
    stored: int,
    updated_at: int,
    now: int,
    cap: int = ENERGY_CAP,
    regen_seconds: int = ENERGY_REGEN_SECONDS,
) -> tuple[int, int]:
    """Lazy regen: returns the post-regen `(energy, updated_at)` pair.

    Sub-regen-tick elapsed time stays unbanked; updated_at advances by
    the consumed ticks only so the next call resumes accurately.
    """
    if stored < 0:
        raise ValueError(f"stored must be >= 0, got {stored}")
    if regen_seconds <= 0:
        raise ValueError(f"regen_seconds must be > 0, got {regen_seconds}")
    if now < updated_at:
        # Clock skew — don't regen backwards, but rebase the timestamp.
        return stored, now
    if stored >= cap:
        return cap, now
    elapsed = now - updated_at
    gained = elapsed // regen_seconds
    if gained == 0:
        return stored, updated_at
    new_stored = min(cap, stored + gained)
    return new_stored, updated_at + gained * regen_seconds


def can_afford(energy: int, cost: int) -> bool:
    return energy >= cost


# -- damage / power -------------------------------------------------------


@dataclass(frozen=True)
class TroopStack:
    """A line in the army inventory used for march-power composition."""

    codename: str
    name: str
    tier: int
    attack: int
    count: int

    @property
    def attack_contribution(self) -> int:
        return self.attack * self.count


def march_power(
    hero_atk: int,
    troops: list[TroopStack],
    troop_attack_pct: int = 0,
    hero_command_pct: int = 0,
) -> int:
    """Compute total march damage for a single attack.

    Hero ATK is the hero's `atk_eff` at its current level (see
    hero_levels.atk_eff) — small, personal contribution. The big lever
    is `hero_command_pct` (hero_levels.command_pct), which multiplies
    the combined hero+troop damage like a leadership buff. Research
    troop_attack_pct stacks on top as a separate multiplier.
    """
    if hero_atk < 0:
        raise ValueError("hero_atk must be >= 0")
    if troop_attack_pct < -99:
        raise ValueError("troop_attack_pct must be > -100")
    if hero_command_pct < -99:
        raise ValueError("hero_command_pct must be > -100")
    troops_atk = sum(stack.attack_contribution for stack in troops)
    base = hero_atk + troops_atk
    # Single rounding step avoids drift from multiplying-then-truncating.
    return int(base * (100 + hero_command_pct) * (100 + troop_attack_pct) / 10000)


def min_power_for_level(level: int) -> int:
    """Hard gate: below this the Attack button is disabled.

    Calibrated so the *worst* run takes ~200 attacks at threshold. Below
    that the grind is absurd even with infinite energy.
    """
    return max(1, get_tenebral(level).hp // 200)


def apply_damage(hp_remaining: int, damage: int) -> int:
    return max(0, hp_remaining - damage)


def is_killed(hp_remaining: int) -> bool:
    return hp_remaining <= 0


# -- faux-timer -----------------------------------------------------------


def faux_march_seconds(
    level: int,
    hero_march_speed_pct: int = 0,
    research_march_speed_pct: int = 0,
) -> int:
    """Apply hero + research march-speed bonuses to the base, then clamp.

    `pct` values are additive (e.g. hero +18%, research +25% -> -43%
    of the base time). Cumulative bonus is capped at 80% so timers never
    fall below ~20% of base; final result clamped between
    MIN_MARCH_SECONDS and MAX_MARCH_SECONDS.
    """
    base = get_tenebral(level).base_march_seconds
    total_pct = max(0, hero_march_speed_pct) + max(0, research_march_speed_pct)
    reduction = min(80, total_pct)
    scaled = int(base * (100 - reduction) / 100)
    return max(MIN_MARCH_SECONDS, min(MAX_MARCH_SECONDS, scaled))


# -- rewards --------------------------------------------------------------


@dataclass(frozen=True)
class KillReward:
    xp: int
    gold: int
    food: int
    wood: int


def kill_reward(level: int) -> KillReward:
    """Split per-kill RSS evenly across gold/food/wood."""
    spec = get_tenebral(level)
    share = spec.reward_rss // 3
    return KillReward(xp=spec.reward_xp, gold=share, food=share, wood=share)
