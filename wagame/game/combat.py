"""Pure auto-battler — Combatant model + `simulate_battle` resolver.

Stateless, deterministic with a seeded RNG. Used by Ghost Arena (async
PvP, future PR) and the `/admin simulate-combat` test harness. No DB,
no Discord — callers assemble `Combatant` from their roster + research
and feed it in.

Model
-----
A Combatant carries:
- one commander hero (its effective ATK + command-pct buff),
- the aggregate troop ATK and HP totals from the army,
- the research-side buffs (troop_attack_pct, troop_hp_pct).

Per-round damage = (hero_atk + troops_atk)
                 * (1 + command/100)
                 * (1 + research_atk/100)

Effective HP    = troops_hp * (1 + research_hp/100)

Heroes don't take HP — only the troops pool absorbs damage. A side
with zero troops gets walked over.

Resolution
----------
Each round both sides roll their damage with ±15% variance (default),
deduct simultaneously, and log the exchange. Hard cap at `max_rounds`
(10 by default); if neither side dies, the higher-HP side wins on
time.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

DEFAULT_VARIANCE = (0.85, 1.15)
DEFAULT_MAX_ROUNDS = 10


@dataclass(frozen=True)
class Combatant:
    name: str               # display label (player's handle, "Bot", etc.)
    hero_name: str
    hero_atk: int
    hero_command_pct: int
    troops_atk: int         # already-summed across the army
    troops_hp: int          # already-summed across the army
    troop_attack_pct: int = 0
    troop_hp_pct: int = 0

    @property
    def round_damage(self) -> int:
        base = self.hero_atk + self.troops_atk
        # Single rounding step avoids drift from chaining multipliers.
        return int(
            base
            * (100 + self.hero_command_pct)
            * (100 + self.troop_attack_pct)
            / 10_000
        )

    @property
    def effective_hp(self) -> int:
        return int(self.troops_hp * (100 + self.troop_hp_pct) / 100)


@dataclass(frozen=True)
class BattleResult:
    winner: str             # combatant name, or "draw"
    rounds: int
    log: list[str]
    final_hp_a: int
    final_hp_b: int


def simulate_battle(
    a: Combatant,
    b: Combatant,
    *,
    rng: random.Random | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    variance: tuple[float, float] = DEFAULT_VARIANCE,
) -> BattleResult:
    """Run the auto-battler.

    `rng` is injectable so callers can replay (e.g. Ghost Arena posting
    the same fight twice) and tests stay deterministic. Variance is the
    `(low, high)` multiplier range applied to each side's damage roll
    independently each round.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be >= 1")
    lo, hi = variance
    if lo > hi or lo < 0:
        raise ValueError("variance must be (low, high) with 0 <= low <= high")

    r = rng or random
    log: list[str] = []

    a_dmg = a.round_damage
    b_dmg = b.round_damage
    a_hp = a.effective_hp
    b_hp = b.effective_hp

    log.append(f"⚔️ **{a.name}** ({a.hero_name}) vs **{b.name}** ({b.hero_name})")
    log.append(f"HP {a_hp:,} vs {b_hp:,} · DMG/round {a_dmg:,} vs {b_dmg:,}")

    # Walkover cases.
    if a_hp <= 0 and b_hp <= 0:
        log.append("💥 Both sides arrived empty-handed.")
        return BattleResult("draw", 0, log, 0, 0)
    if a_hp <= 0:
        log.append(f"🏆 **{b.name}** wins by walkover.")
        return BattleResult(b.name, 0, log, 0, b_hp)
    if b_hp <= 0:
        log.append(f"🏆 **{a.name}** wins by walkover.")
        return BattleResult(a.name, 0, log, a_hp, 0)

    rounds = 0
    while rounds < max_rounds and a_hp > 0 and b_hp > 0:
        rounds += 1
        a_roll = int(a_dmg * r.uniform(lo, hi))
        b_roll = int(b_dmg * r.uniform(lo, hi))
        a_hp -= b_roll
        b_hp -= a_roll
        log.append(
            f"R{rounds}: {a.name} hits {a_roll:,}, "
            f"{b.name} hits {b_roll:,}. "
            f"HP {max(0, a_hp):,} vs {max(0, b_hp):,}"
        )

    # Outcome.
    if a_hp <= 0 and b_hp <= 0:
        winner = "draw"
        log.append(f"💥 Mutual destruction in round {rounds}.")
    elif a_hp <= 0:
        winner = b.name
        log.append(f"🏆 **{b.name}** wins in round {rounds}.")
    elif b_hp <= 0:
        winner = a.name
        log.append(f"🏆 **{a.name}** wins in round {rounds}.")
    else:
        if a_hp > b_hp:
            winner = a.name
            log.append(
                f"⏱ Time. **{a.name}** wins on HP ({a_hp:,} vs {b_hp:,})."
            )
        elif b_hp > a_hp:
            winner = b.name
            log.append(
                f"⏱ Time. **{b.name}** wins on HP ({b_hp:,} vs {a_hp:,})."
            )
        else:
            winner = "draw"
            log.append(f"⏱ Time. Draw at {a_hp:,} HP each.")

    return BattleResult(
        winner=winner,
        rounds=rounds,
        log=log,
        final_hp_a=max(0, a_hp),
        final_hp_b=max(0, b_hp),
    )
