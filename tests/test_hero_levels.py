"""Tests for the hero leveling math (wagame.game.hero_levels)."""

from __future__ import annotations

import pytest

from wagame.game.hero_levels import (
    ATK_PCT_PER_LEVEL,
    MARCH_SPEED_PCT_PER_LEVEL,
    RARITY_BASE_ATK,
    STARTING_LEVEL_CAP,
    apply_xp_gain,
    atk_eff,
    base_atk_for_rarity,
    march_speed_pct,
    xp_to_next,
)

# -- xp_to_next -----------------------------------------------------------


def test_xp_to_next_level_one_costs_100() -> None:
    assert xp_to_next(1) == 100


def test_xp_to_next_grows_super_linearly() -> None:
    assert xp_to_next(10) == 3162  # floor(100 * 10**1.5)
    assert xp_to_next(29) == 15616


def test_xp_to_next_is_monotonic() -> None:
    last = 0
    for lv in range(1, STARTING_LEVEL_CAP):
        cost = xp_to_next(lv)
        assert cost > last
        last = cost


def test_xp_to_next_rejects_level_zero() -> None:
    with pytest.raises(ValueError):
        xp_to_next(0)


# -- base_atk + atk_eff ---------------------------------------------------


def test_base_atk_covers_all_known_rarities() -> None:
    for rarity, expected in RARITY_BASE_ATK.items():
        assert base_atk_for_rarity(rarity) == expected


def test_base_atk_falls_back_to_common_for_unknowns() -> None:
    assert base_atk_for_rarity("nonsense") == RARITY_BASE_ATK["common"]
    assert base_atk_for_rarity(None) == RARITY_BASE_ATK["common"]


def test_atk_eff_at_level_one_matches_baseline() -> None:
    for rarity, expected in RARITY_BASE_ATK.items():
        assert atk_eff(rarity, 1) == expected


def test_atk_eff_scales_with_level() -> None:
    # +5% per level, additive over baseline 300 -> Lv10 = 300 * 1.45 = 435.
    assert atk_eff("legendary", 10) == 435
    # Lv30 multiplier = 1 + 29 * 0.05 = 2.45 -> mythic 500 * 2.45 = 1225.
    assert atk_eff("mythic", 30) == 1225


def test_atk_pct_per_level_is_five() -> None:
    # Sanity-check the contract — combat code reads this constant.
    assert ATK_PCT_PER_LEVEL == 5


# -- march_speed_pct ------------------------------------------------------


def test_march_speed_at_level_one_is_zero() -> None:
    assert march_speed_pct(1) == 0


def test_march_speed_grows_linearly() -> None:
    assert march_speed_pct(10) == 18
    assert march_speed_pct(STARTING_LEVEL_CAP) == 58


def test_march_speed_pct_per_level_constant() -> None:
    assert MARCH_SPEED_PCT_PER_LEVEL == 2


# -- apply_xp_gain --------------------------------------------------------


def test_apply_xp_gain_below_threshold_just_banks() -> None:
    result = apply_xp_gain(level=1, xp=0, gained=50)
    assert result.new_level == 1
    assert result.new_xp == 50
    assert result.levels_gained == 0


def test_apply_xp_gain_exact_threshold_levels_once() -> None:
    result = apply_xp_gain(level=1, xp=0, gained=100)
    assert result.new_level == 2
    assert result.new_xp == 0
    assert result.levels_gained == 1


def test_apply_xp_gain_cascades_multiple_levels() -> None:
    # 100 + 282 + 519 = 901 < 1000; 4th level (lv5) needs 894 -> overshoots.
    result = apply_xp_gain(level=1, xp=0, gained=1000)
    assert result.levels_gained >= 2
    assert result.new_level == 1 + result.levels_gained


def test_apply_xp_gain_carries_leftover_xp() -> None:
    # 150 XP grants Lv1->2 (cost 100) leaves 50 banked at Lv2.
    result = apply_xp_gain(level=1, xp=0, gained=150)
    assert result.new_level == 2
    assert result.new_xp == 50
    assert result.levels_gained == 1


def test_apply_xp_gain_respects_starting_xp() -> None:
    # Already has 80 XP, needs 20 more for Lv1->2.
    result = apply_xp_gain(level=1, xp=80, gained=20)
    assert result.new_level == 2
    assert result.new_xp == 0


def test_apply_xp_gain_banks_excess_at_cap() -> None:
    # At cap, any gained XP banks on the row without leveling further.
    result = apply_xp_gain(
        level=STARTING_LEVEL_CAP, xp=200, gained=5000, cap=STARTING_LEVEL_CAP
    )
    assert result.new_level == STARTING_LEVEL_CAP
    assert result.new_xp == 5200
    assert result.levels_gained == 0


def test_apply_xp_gain_stops_exactly_at_cap_with_overflow_banked() -> None:
    # Custom low cap forces hitting it mid-grant.
    result = apply_xp_gain(level=1, xp=0, gained=10_000_000, cap=3)
    assert result.new_level == 3
    assert result.levels_gained == 2
    # leftover = 10_000_000 - xp_to_next(1) - xp_to_next(2)
    assert result.new_xp == 10_000_000 - xp_to_next(1) - xp_to_next(2)


def test_apply_xp_gain_zero_is_noop() -> None:
    result = apply_xp_gain(level=5, xp=42, gained=0)
    assert result.new_level == 5
    assert result.new_xp == 42
    assert result.levels_gained == 0


def test_apply_xp_gain_rejects_negative_amount() -> None:
    with pytest.raises(ValueError):
        apply_xp_gain(level=1, xp=0, gained=-1)


def test_apply_xp_gain_rejects_invalid_state() -> None:
    with pytest.raises(ValueError):
        apply_xp_gain(level=0, xp=0, gained=10)
    with pytest.raises(ValueError):
        apply_xp_gain(level=1, xp=-1, gained=10)


def test_cap_raise_consumes_banked_xp() -> None:
    # Simulate research raising the cap from 3 to 6 — banked XP should
    # cascade level-ups on the next call without granting more.
    first = apply_xp_gain(level=1, xp=0, gained=10_000, cap=3)
    assert first.new_level == 3
    banked = first.new_xp
    assert banked > 0

    second = apply_xp_gain(level=first.new_level, xp=banked, gained=0, cap=6)
    assert second.new_level > 3
    assert second.levels_gained == second.new_level - 3
