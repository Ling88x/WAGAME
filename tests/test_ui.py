"""Tests for the shared UI helpers (color/icon/Flash plumbing)."""

from __future__ import annotations

import discord

from wagame.ui import (
    NEUTRAL_COLOR,
    Flash,
    Outcome,
    apply_flash,
    color_for,
    icon_for,
    toast,
)


def test_outcome_has_distinct_color_for_each_state() -> None:
    seen = {color_for(o).value for o in Outcome}
    assert len(seen) == len(Outcome)


def test_icons_are_present_for_signal_outcomes() -> None:
    assert icon_for(Outcome.NEUTRAL) == ""
    assert icon_for(Outcome.SUCCESS)
    assert icon_for(Outcome.ERROR)
    assert icon_for(Outcome.INFO)


def test_flash_helpers_pick_the_right_outcome() -> None:
    assert Flash.ok("hi").outcome == Outcome.SUCCESS
    assert Flash.err("nope").outcome == Outcome.ERROR
    assert Flash.info("fyi").outcome == Outcome.INFO


def test_flash_formatted_includes_icon_for_signal_states() -> None:
    assert "✅" in Flash.ok("done").formatted()
    assert "❌" in Flash.err("boom").formatted()


def test_apply_flash_none_resets_to_neutral_and_leaves_description() -> None:
    embed = discord.Embed(title="Panel")
    apply_flash(embed, None)
    assert embed.color == NEUTRAL_COLOR
    assert embed.description in (None, "")


def test_apply_flash_error_paints_red_and_prepends_message() -> None:
    embed = discord.Embed(title="Panel", description="prior body")
    apply_flash(embed, Flash.err("not enough gold"))
    assert embed.color == color_for(Outcome.ERROR)
    assert embed.description is not None
    # Message comes first so the player notices it immediately.
    assert embed.description.startswith("**")
    assert "not enough gold" in embed.description
    # Existing body preserved on a new line.
    assert "prior body" in embed.description


def test_apply_flash_success_returns_the_same_embed_for_chaining() -> None:
    embed = discord.Embed()
    returned = apply_flash(embed, Flash.ok("done"))
    assert returned is embed
    assert embed.color == color_for(Outcome.SUCCESS)


def test_toast_builds_one_shot_embed() -> None:
    embed = toast("hello", Outcome.INFO)
    assert embed.color == color_for(Outcome.INFO)
    assert embed.description is not None
    assert "hello" in embed.description
