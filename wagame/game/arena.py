"""Pure logic for Ghost Arena — ELO ratings + reward math.

Standard ELO with K-factor 32 and a starting rating of 1000. `apply_elo`
returns the new ratings for both sides given the outcome encoded as
`score_a` in {0.0, 0.5, 1.0}. The cog does the DB write + reward credit
on top.

Rewards (gems):
- Win:  100
- Draw:  50
- Loss:  25 (consolation so the loser doesn't feel mugged)
"""

from __future__ import annotations

from dataclasses import dataclass

ARENA_K_FACTOR = 32
ARENA_STARTING_RATING = 1000
ARENA_RATING_FLOOR = 100

GEMS_WIN = 100
GEMS_DRAW = 50
GEMS_LOSS = 25


@dataclass(frozen=True)
class RatingChange:
    new_a: int
    new_b: int
    delta_a: int
    delta_b: int


def expected_score(a_rating: int, b_rating: int) -> float:
    """ELO expected score for side A given the two ratings."""
    return 1 / (1 + 10 ** ((b_rating - a_rating) / 400))


def apply_elo(
    a_rating: int,
    b_rating: int,
    score_a: float,
    *,
    k_factor: int = ARENA_K_FACTOR,
    floor: int = ARENA_RATING_FLOOR,
) -> RatingChange:
    """Apply ELO update to both ratings.

    `score_a` is 1.0 (A won), 0.5 (draw), or 0.0 (B won). Ratings clamp
    at `floor` so a long losing streak can't sink someone below the
    welcome mat.
    """
    if score_a not in (0.0, 0.5, 1.0):
        raise ValueError("score_a must be 0.0, 0.5, or 1.0")
    if k_factor <= 0:
        raise ValueError("k_factor must be > 0")
    ea = expected_score(a_rating, b_rating)
    new_a_raw = a_rating + k_factor * (score_a - ea)
    new_b_raw = b_rating + k_factor * ((1 - score_a) - (1 - ea))
    new_a = max(floor, round(new_a_raw))
    new_b = max(floor, round(new_b_raw))
    return RatingChange(
        new_a=new_a,
        new_b=new_b,
        delta_a=new_a - a_rating,
        delta_b=new_b - b_rating,
    )


def reward_gems(score_a: float) -> tuple[int, int]:
    """Return `(gems_for_a, gems_for_b)` given the match outcome."""
    if score_a == 1.0:
        return GEMS_WIN, GEMS_LOSS
    if score_a == 0.0:
        return GEMS_LOSS, GEMS_WIN
    return GEMS_DRAW, GEMS_DRAW
