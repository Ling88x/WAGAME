"""Research node catalog. Nodes are static; per-player progress lives in DB.

Each node specifies the gold cost and duration *per level* (length of the
tuple = `max_level`). Effects are linear: at completed level N, the player
receives `effect_per_level * N` added to `effect_column`. For toggles like
"unlock the next troop tier", that's still just `+1` to `unlocked_tier`
per level.

When the balance pass happens we'll likely rewrite these tuples wholesale —
the cog reads them directly so changes here propagate without migrations.
"""

from __future__ import annotations

from dataclasses import dataclass

TRACK_LABEL = {
    "economy":   "Economy",
    "military":  "Military",
    "logistics": "Logistics",
}


@dataclass(frozen=True)
class ResearchNode:
    codename: str
    name: str
    description: str
    track: str                   # 'economy' | 'military' | 'logistics'
    costs: tuple[int, ...]       # gold cost to research level i+1 (0-indexed)
    durations: tuple[int, ...]   # seconds for the same
    effect_column: str           # which players.<col> this node modifies
    effect_per_level: int        # delta added per completed level
    requires: str | None         # prereq node codename, or None
    requires_level: int          # min level of prereq needed
    sort_order: int

    @property
    def max_level(self) -> int:
        return len(self.costs)

    def cost_at(self, level: int) -> int:
        """Gold cost to go from level-1 to level. 1-indexed."""
        if level < 1 or level > self.max_level:
            raise ValueError(f"{self.codename}: level {level} out of range [1, {self.max_level}]")
        return self.costs[level - 1]

    def duration_at(self, level: int) -> int:
        if level < 1 or level > self.max_level:
            raise ValueError(f"{self.codename}: level {level} out of range [1, {self.max_level}]")
        return self.durations[level - 1]


NODES: tuple[ResearchNode, ...] = (
    # -- economy ----------------------------------------------------------
    ResearchNode(
        codename="gather_yield",
        name="Gather Yield",
        description="+5% yield from gathering marches per level.",
        track="economy",
        costs=(500, 1000, 2000, 4000, 8000),
        durations=(300, 600, 1200, 2400, 4800),
        effect_column="gather_yield_pct",
        effect_per_level=5,
        requires=None,
        requires_level=0,
        sort_order=10,
    ),
    ResearchNode(
        codename="gather_speed",
        name="Gather Speed",
        description="-3% gather march duration per level.",
        track="economy",
        costs=(800, 1500, 3000, 6000, 12000),
        durations=(600, 1200, 2400, 4800, 9600),
        effect_column="gather_speed_pct",
        effect_per_level=3,
        requires=None,
        requires_level=0,
        sort_order=20,
    ),
    ResearchNode(
        codename="training_queue",
        name="Summoning Queue",
        description="+50 batch capacity at the Summoning Gate per level.",
        track="economy",
        costs=(1000, 2000, 4000, 8000, 16000),
        durations=(900, 1800, 3600, 7200, 14400),
        effect_column="training_queue_cap",
        effect_per_level=50,
        requires=None,
        requires_level=0,
        sort_order=30,
    ),
    ResearchNode(
        codename="training_speed",
        name="Summoning Speed",
        description="+3% Summoning Gate training speed per level.",
        track="economy",
        costs=(1500, 3000, 6000, 12000, 24000),
        durations=(1200, 2400, 4800, 9600, 19200),
        effect_column="training_speed_boost_pct",
        effect_per_level=3,
        requires="training_queue",
        requires_level=2,
        sort_order=40,
    ),

    # -- military ---------------------------------------------------------
    ResearchNode(
        codename="troop_tier",
        name="Higher Tier Summons",
        description="Unlock the next troop tier (T2, T3, T4).",
        track="military",
        costs=(2000, 8000, 32000),
        durations=(1800, 7200, 28800),
        effect_column="unlocked_tier",
        effect_per_level=1,
        requires=None,
        requires_level=0,
        sort_order=50,
    ),
    ResearchNode(
        codename="troop_attack",
        name="Troop Attack",
        description="+5% troop attack in combat per level. (Used once combat ships.)",
        track="military",
        costs=(1000, 2000, 4000, 8000, 16000),
        durations=(1800, 3600, 7200, 14400, 28800),
        effect_column="troop_attack_pct",
        effect_per_level=5,
        requires="troop_tier",
        requires_level=1,
        sort_order=60,
    ),
    ResearchNode(
        codename="troop_hp",
        name="Troop HP",
        description="+5% troop HP in combat per level. (Used once combat ships.)",
        track="military",
        costs=(1000, 2000, 4000, 8000, 16000),
        durations=(1800, 3600, 7200, 14400, 28800),
        effect_column="troop_hp_pct",
        effect_per_level=5,
        requires="troop_tier",
        requires_level=1,
        sort_order=70,
    ),

    # -- logistics --------------------------------------------------------
    ResearchNode(
        codename="march_capacity",
        name="March Slots",
        description="+1 concurrent gather march slot per level (2 -> 6).",
        track="logistics",
        costs=(1000, 3000, 9000, 27000),
        durations=(1800, 3600, 7200, 14400),
        effect_column="march_capacity",
        effect_per_level=1,
        requires=None,
        requires_level=0,
        sort_order=80,
    ),
)

NODES_BY_CODENAME: dict[str, ResearchNode] = {n.codename: n for n in NODES}


def get_node(codename: str) -> ResearchNode | None:
    return NODES_BY_CODENAME.get(codename)
