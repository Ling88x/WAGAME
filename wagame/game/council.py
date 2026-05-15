"""Pure logic for the Witch's Council — quest definitions + reward math.

One active quest at a time globally. The cog spawns a new quest the
moment the previous one settles, so there's always something to chase.

Quest kinds
-----------
- KILL_TENEBRALS: +1 per tenebral kill (hunt or sighting).
- GATHER_RSS:     +(gold + food + wood) per claimed gather march.
- COMPLETE_DAILIES: +1 per daily-quest reward claim.

Targets are tuned for a casual 5-10-active-player server over 7 days.
Each player contributing ~weekly average lands the bar at ~80-100%.

Rewards
-------
Quest completed (progress >= target):
- Every contributor with ≥1% of target gets COMPLETE_REWARD_GEMS gems
  and COMPLETE_REWARD_SHARDS random hero shards.
- Top contributor adds TOP_CONTRIBUTOR_BONUS gems on top.

Quest failed:
- Every contributor with ≥1% of target gets FAIL_CONSOLATION_GEMS gems.
- Top contributor still grabs TOP_CONTRIBUTOR_BONUS / 2.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

QuestKind = Literal["kill_tenebrals", "gather_rss", "complete_dailies"]
QUEST_KINDS: tuple[QuestKind, ...] = (
    "kill_tenebrals",
    "gather_rss",
    "complete_dailies",
)

QUEST_DURATION_SECONDS = 7 * 24 * 3600

# Tuned for a 5-10 active player server over 7 days.
TARGET_BY_KIND: dict[QuestKind, int] = {
    "kill_tenebrals":   300,
    "gather_rss":       8_000_000,
    "complete_dailies":  20,
}

# Reward thresholds expressed as a percent of the target.
CONTRIBUTOR_THRESHOLD_PCT = 1  # ≥1% counts as a contributor.

COMPLETE_REWARD_GEMS = 500
COMPLETE_REWARD_SHARDS = 3
FAIL_CONSOLATION_GEMS = 100
TOP_CONTRIBUTOR_BONUS = 500


@dataclass(frozen=True)
class ContributorAward:
    user_id: int
    amount: int
    gems: int
    shards: int


def pick_kind(rng: random.Random | None = None) -> QuestKind:
    r = rng or random
    return r.choice(QUEST_KINDS)


def target_for(kind: QuestKind) -> int:
    return TARGET_BY_KIND[kind]


def is_completed(progress: int, target: int) -> bool:
    return progress >= target


def threshold_amount(target: int) -> int:
    """Minimum contribution amount to count as a contributor."""
    return max(1, target * CONTRIBUTOR_THRESHOLD_PCT // 100)


def split_rewards(
    contributions: list[tuple[int, int]],
    target: int,
    *,
    completed: bool,
) -> list[ContributorAward]:
    """Decide what every contributor walks away with.

    `contributions` is `[(user_id, amount), ...]` sorted high-to-low by
    amount; the first entry is the top contributor.
    """
    threshold = threshold_amount(target)
    qualifying = [(uid, amt) for uid, amt in contributions if amt >= threshold]
    if not qualifying:
        return []

    base_gems = COMPLETE_REWARD_GEMS if completed else FAIL_CONSOLATION_GEMS
    base_shards = COMPLETE_REWARD_SHARDS if completed else 0
    top_bonus = TOP_CONTRIBUTOR_BONUS if completed else TOP_CONTRIBUTOR_BONUS // 2
    top_uid = qualifying[0][0]

    awards: list[ContributorAward] = []
    for uid, amt in qualifying:
        gems = base_gems + (top_bonus if uid == top_uid else 0)
        awards.append(
            ContributorAward(
                user_id=uid, amount=amt, gems=gems, shards=base_shards,
            )
        )
    return awards


KIND_LABELS: dict[QuestKind, str] = {
    "kill_tenebrals":   "Slay Tenebrals",
    "gather_rss":       "Gather Resources",
    "complete_dailies": "Complete Daily Quests",
}


KIND_UNIT_LABELS: dict[QuestKind, str] = {
    "kill_tenebrals":   "kills",
    "gather_rss":       "RSS",
    "complete_dailies": "quests",
}


def describe(kind: QuestKind) -> str:
    return KIND_LABELS[kind]


def unit_label(kind: QuestKind) -> str:
    return KIND_UNIT_LABELS[kind]
