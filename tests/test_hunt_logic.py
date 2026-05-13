"""Tests for wagame.game.hunt — pure logic, no DB."""

from __future__ import annotations

import pytest

from wagame.game.hunt import (
    DAILY_QUOTA_KILLS,
    ENERGY_CAP,
    ENERGY_REGEN_SECONDS,
    MAX_MARCH_SECONDS,
    MIN_MARCH_SECONDS,
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

# -- tenebral table -------------------------------------------------------


def test_tenebral_table_has_twelve_levels() -> None:
    levels = [t.level for t in TENEBRAL_TABLE]
    assert levels == list(range(1, 13))


def test_tenebral_hp_strictly_increases() -> None:
    last = 0
    for t in TENEBRAL_TABLE:
        assert t.hp > last, f"lv{t.level} hp {t.hp} not > {last}"
        last = t.hp


def test_tenebral_energy_cost_strictly_increases() -> None:
    last = 0
    for t in TENEBRAL_TABLE:
        assert t.energy_cost > last
        last = t.energy_cost


def test_get_tenebral_round_trip() -> None:
    assert get_tenebral(1).hp == 750
    assert get_tenebral(12).hp == 100_000_000_000


def test_get_tenebral_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        get_tenebral(0)
    with pytest.raises(ValueError):
        get_tenebral(13)


# -- energy regen ---------------------------------------------------------


def test_regen_no_time_elapsed_is_noop() -> None:
    energy, updated_at = regen_energy(stored=100, updated_at=1000, now=1000)
    assert energy == 100
    assert updated_at == 1000


def test_regen_below_tick_returns_unchanged() -> None:
    # 29 seconds < 30 — no full tick yet.
    energy, updated_at = regen_energy(stored=100, updated_at=1000, now=1029)
    assert energy == 100
    assert updated_at == 1000


def test_regen_one_tick_grants_one_point() -> None:
    energy, updated_at = regen_energy(stored=100, updated_at=1000, now=1030)
    assert energy == 101
    assert updated_at == 1030


def test_regen_two_ticks_grants_two_and_banks_remainder() -> None:
    # 65s elapsed: 2 full ticks + 5s leftover. Updated_at advances 60s.
    energy, updated_at = regen_energy(stored=100, updated_at=1000, now=1065)
    assert energy == 102
    assert updated_at == 1060


def test_regen_caps_at_max() -> None:
    energy, updated_at = regen_energy(stored=499, updated_at=1000, now=10_000)
    assert energy == ENERGY_CAP
    # Already at cap means we don't track sub-cap progress.
    assert updated_at == 10_000


def test_regen_at_cap_rebases_timestamp() -> None:
    energy, updated_at = regen_energy(stored=ENERGY_CAP, updated_at=1000, now=9999)
    assert energy == ENERGY_CAP
    assert updated_at == 9999


def test_regen_clock_skew_rebases_safely() -> None:
    energy, updated_at = regen_energy(stored=100, updated_at=2000, now=1000)
    assert energy == 100
    assert updated_at == 1000


def test_regen_rejects_negative_state() -> None:
    with pytest.raises(ValueError):
        regen_energy(stored=-1, updated_at=0, now=100)


def test_regen_seconds_constant() -> None:
    assert ENERGY_REGEN_SECONDS == 30


def test_can_afford() -> None:
    assert can_afford(100, 25)
    assert can_afford(25, 25)
    assert not can_afford(24, 25)


# -- march power ----------------------------------------------------------


def test_march_power_hero_alone() -> None:
    assert march_power(hero_atk=300, troops=[]) == 300


def test_march_power_sums_troops() -> None:
    troops = [
        TroopStack("catsith", "Catsith", 1, attack=60, count=100),
        TroopStack("gryphon", "Gryphon", 2, attack=140, count=50),
    ]
    # 60*100 + 140*50 + hero 200 = 6000 + 7000 + 200 = 13_200
    assert march_power(hero_atk=200, troops=troops) == 13_200


def test_march_power_applies_research_buff() -> None:
    troops = [TroopStack("dawon", "Dawon", 4, attack=200, count=100)]
    # 200*100 + hero 0 = 20_000; +50% research -> 30_000.
    assert march_power(hero_atk=0, troops=troops, troop_attack_pct=50) == 30_000


def test_march_power_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError):
        march_power(hero_atk=-1, troops=[])
    with pytest.raises(ValueError):
        march_power(hero_atk=0, troops=[], troop_attack_pct=-150)


def test_min_power_for_level_scales_with_hp() -> None:
    # lv1 hp 750 / 200 = 3, min 1 floor -> 3.
    assert min_power_for_level(1) == 3
    # lv12 hp 100B / 200 = 500M.
    assert min_power_for_level(12) == 500_000_000


def test_min_power_for_level_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        min_power_for_level(99)


# -- damage --------------------------------------------------------------


def test_apply_damage_chips_hp() -> None:
    assert apply_damage(1000, 250) == 750


def test_apply_damage_floors_at_zero() -> None:
    assert apply_damage(100, 1_000_000) == 0


def test_is_killed() -> None:
    assert is_killed(0)
    assert not is_killed(1)


# -- faux-timer ----------------------------------------------------------


def test_faux_march_seconds_uses_base_when_no_bonus() -> None:
    # lv1 base 60s, clamps to MAX 90 -> 60 stays.
    assert faux_march_seconds(1) == 60


def test_faux_march_seconds_applies_hero_bonus() -> None:
    # 60 * (1 - 0.50) = 30
    assert faux_march_seconds(1, hero_march_speed_pct=50) == 30


def test_faux_march_seconds_stacks_research_and_hero() -> None:
    # hero 30 + research 30 = 60% reduction -> 60 * 0.4 = 24.
    assert faux_march_seconds(1, hero_march_speed_pct=30, research_march_speed_pct=30) == 24


def test_faux_march_seconds_clamps_to_min() -> None:
    assert faux_march_seconds(1, hero_march_speed_pct=999) == MIN_MARCH_SECONDS


def test_faux_march_seconds_clamps_to_max() -> None:
    # If a future tenebral had a huge base_march, we'd still clamp -
    # but all current entries fit inside the band, so just sanity-check
    # that MIN <= result <= MAX for every level + zero bonuses.
    for t in TENEBRAL_TABLE:
        seconds = faux_march_seconds(t.level)
        assert MIN_MARCH_SECONDS <= seconds <= MAX_MARCH_SECONDS


# -- rewards -------------------------------------------------------------


def test_kill_reward_splits_rss_evenly() -> None:
    reward = kill_reward(2)
    spec = get_tenebral(2)
    share = spec.reward_rss // 3
    assert reward.gold == share
    assert reward.food == share
    assert reward.wood == share
    assert reward.xp == spec.reward_xp


def test_kill_reward_xp_increases_with_level() -> None:
    last = 0
    for level in range(1, 13):
        xp = kill_reward(level).xp
        assert xp > last
        last = xp


def test_daily_quota_constant() -> None:
    assert DAILY_QUOTA_KILLS == 10
