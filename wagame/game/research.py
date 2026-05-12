"""Pure logic for the Research system — prereq + cost lookup.

Effect application is a one-liner against the player row, so the cog calls
`apply_completed_level` directly rather than going through a helper here.
"""

from __future__ import annotations

from dataclasses import dataclass

from wagame.research_data import ResearchNode


@dataclass(frozen=True)
class ResearchPlan:
    node_codename: str
    target_level: int
    gold_cost: int
    total_seconds: int


def next_level(current_level: int, node: ResearchNode) -> int | None:
    """Returns the next researchable level, or None if maxed."""
    if current_level >= node.max_level:
        return None
    return current_level + 1


def prereq_met(node: ResearchNode, prereq_current_level: int) -> bool:
    """Whether `node`'s prereq is satisfied given the prereq node's current level.

    Returns True for nodes without a prereq. The caller passes 0 when the
    prereq isn't started yet.
    """
    if node.requires is None:
        return True
    return prereq_current_level >= node.requires_level


def plan_research(node: ResearchNode, target_level: int) -> ResearchPlan:
    """Returns cost/duration for advancing to `target_level` of `node`."""
    return ResearchPlan(
        node_codename=node.codename,
        target_level=target_level,
        gold_cost=node.cost_at(target_level),
        total_seconds=node.duration_at(target_level),
    )


def format_duration(seconds: int) -> str:
    """`12s`, `4m 03s`, `2h 15m`, `3d 04h`. Empty when zero or negative."""
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"
