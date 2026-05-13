"""Shared UI helpers — outcome colors / icons, applied uniformly across cogs.

Every panel and toast routes its flash message through `Flash` so users
get the same visual signal everywhere: red = error, green = success, the
neutral grey = idle, etc. The cog stays free to add accent fields on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import discord

# Neutral default — every panel uses this when there's no in-flight event.
# We intentionally pick a desaturated colour so it doesn't fight with the
# success/error tones when they appear.
NEUTRAL_COLOR = discord.Color.from_rgb(99, 110, 123)


class Outcome(str, Enum):
    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    ERROR = "error"


_COLORS: dict[Outcome, discord.Color] = {
    Outcome.NEUTRAL: NEUTRAL_COLOR,
    Outcome.INFO:    discord.Color.from_rgb(88, 166, 255),   # calm blue
    Outcome.SUCCESS: discord.Color.from_rgb(63, 185, 80),    # green
    Outcome.ERROR:   discord.Color.from_rgb(248, 81, 73),    # red
}

_ICONS: dict[Outcome, str] = {
    Outcome.NEUTRAL: "",
    Outcome.INFO:    "ℹ️",  # noqa: RUF001 — actual emoji, not a Latin "i"
    Outcome.SUCCESS: "✅",
    Outcome.ERROR:   "❌",
}


def color_for(outcome: Outcome) -> discord.Color:
    return _COLORS[outcome]


def icon_for(outcome: Outcome) -> str:
    return _ICONS[outcome]


@dataclass(frozen=True)
class Flash:
    """Short status line shown at the top of a panel or as a toast embed."""

    outcome: Outcome
    message: str

    def formatted(self) -> str:
        icon = _ICONS[self.outcome]
        return f"{icon} {self.message}".strip()

    @classmethod
    def ok(cls, message: str) -> Flash:
        return cls(Outcome.SUCCESS, message)

    @classmethod
    def err(cls, message: str) -> Flash:
        return cls(Outcome.ERROR, message)

    @classmethod
    def info(cls, message: str) -> Flash:
        return cls(Outcome.INFO, message)


def apply_flash(embed: discord.Embed, flash: Flash | None) -> discord.Embed:
    """Set the embed's color and prepend an icon to its description.

    Pass `None` to get the neutral idle look. Mutates and returns `embed`
    for convenience.
    """
    if flash is None:
        embed.color = NEUTRAL_COLOR
        return embed

    embed.color = _COLORS[flash.outcome]
    prefix = f"**{flash.formatted()}**"
    if embed.description:
        embed.description = f"{prefix}\n{embed.description}"
    else:
        embed.description = prefix
    return embed


def toast(message: str, outcome: Outcome = Outcome.INFO) -> discord.Embed:
    """Build a one-off ephemeral embed (used outside the persistent panels)."""
    embed = discord.Embed(description=f"{icon_for(outcome)} {message}".strip())
    embed.color = _COLORS[outcome]
    return embed
