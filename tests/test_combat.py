"""Tests for the auto-battler (wagame.game.combat)."""

from __future__ import annotations

import random

import pytest

from wagame.game.combat import (
    DEFAULT_MAX_ROUNDS,
    BattleResult,
    Combatant,
    simulate_battle,
)


def _make(
    name: str = "A",
    hero: str = "Vivi",
    hero_atk: int = 1_000,
    command_pct: int = 0,
    troops_atk: int = 50_000,
    troops_hp: int = 200_000,
    atk_pct: int = 0,
    hp_pct: int = 0,
) -> Combatant:
    return Combatant(
        name=name,
        hero_name=hero,
        hero_atk=hero_atk,
        hero_command_pct=command_pct,
        troops_atk=troops_atk,
        troops_hp=troops_hp,
        troop_attack_pct=atk_pct,
        troop_hp_pct=hp_pct,
    )


# -- combatant math -----------------------------------------------------


def test_round_damage_sums_hero_and_troops() -> None:
    c = _make(hero_atk=1_000, troops_atk=50_000)
    assert c.round_damage == 51_000


def test_round_damage_applies_command_and_research_multipliers() -> None:
    c = _make(
        hero_atk=0, troops_atk=10_000,
        command_pct=50, atk_pct=20,
    )
    # 10_000 * 1.5 * 1.2 = 18_000
    assert c.round_damage == 18_000


def test_effective_hp_applies_research_hp_pct() -> None:
    c = _make(troops_hp=100_000, hp_pct=25)
    assert c.effective_hp == 125_000


# -- simulate_battle ----------------------------------------------------


def test_simulate_battle_is_deterministic_with_seed() -> None:
    a = _make("A")
    b = _make("B")
    r1 = simulate_battle(a, b, rng=random.Random(42))
    r2 = simulate_battle(a, b, rng=random.Random(42))
    assert r1.winner == r2.winner
    assert r1.rounds == r2.rounds
    assert r1.log == r2.log
    assert r1.final_hp_a == r2.final_hp_a
    assert r1.final_hp_b == r2.final_hp_b


def test_zero_troops_loses_by_walkover() -> None:
    a = _make("A", troops_atk=10, troops_hp=0)
    b = _make("B")
    result = simulate_battle(a, b, rng=random.Random(0))
    assert result.winner == "B"
    assert result.rounds == 0
    assert any("walkover" in line for line in result.log)


def test_both_empty_is_draw() -> None:
    a = _make("A", troops_hp=0)
    b = _make("B", troops_hp=0)
    result = simulate_battle(a, b, rng=random.Random(0))
    assert result.winner == "draw"
    assert result.rounds == 0


def test_stronger_side_typically_wins() -> None:
    """Stat-disparity sanity: 10x stronger side wins overwhelmingly."""
    strong = _make("Strong", troops_atk=500_000, troops_hp=2_000_000)
    weak = _make("Weak", troops_atk=10_000, troops_hp=100_000)
    wins = sum(
        1
        for seed in range(50)
        if simulate_battle(strong, weak, rng=random.Random(seed)).winner == "Strong"
    )
    assert wins >= 48


def test_log_structure_has_header_rounds_and_outcome() -> None:
    a = _make("A")
    b = _make("B")
    result = simulate_battle(a, b, rng=random.Random(0))
    assert result.log[0].startswith("⚔️")
    # At least one round line.
    assert any(line.startswith("R") for line in result.log)
    # Final line is one of the outcome markers.
    final = result.log[-1]
    assert any(token in final for token in ("🏆", "💥", "⏱"))


def test_max_rounds_cap_triggers_hp_tiebreak() -> None:
    # Two tanks: huge HP, tiny DMG. Battle never resolves inside 10 rounds.
    a = _make("A", troops_atk=100, troops_hp=10_000_000)
    b = _make("B", troops_atk=100, troops_hp=10_000_000)
    result = simulate_battle(a, b, rng=random.Random(0), max_rounds=5)
    assert result.rounds == 5
    # Either side wins on HP or it's a draw — outcome line must reflect time.
    assert "Time" in result.log[-1]


def test_simulate_battle_rejects_bad_inputs() -> None:
    a = _make("A")
    b = _make("B")
    with pytest.raises(ValueError):
        simulate_battle(a, b, max_rounds=0)
    with pytest.raises(ValueError):
        simulate_battle(a, b, variance=(1.2, 0.8))


def test_returns_proper_dataclass() -> None:
    result = simulate_battle(_make("A"), _make("B"), rng=random.Random(0))
    assert isinstance(result, BattleResult)


def test_default_max_rounds_is_ten() -> None:
    assert DEFAULT_MAX_ROUNDS == 10
