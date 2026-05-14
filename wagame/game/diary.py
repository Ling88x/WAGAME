"""Hero Diary — pure logic for picking a flavor recap line.

Each gameplay event (hunt kill, hunt chip, gather claim, level-up)
seeds a pick from a small corpus of in-character lines living in
`wagame/data/diary_lines.json`. Templates use Python `str.format`
placeholders: `{hero}`, `{mob}`, `{level}`, `{rss}`. Unknown
placeholders in a line raise on render — keeps the corpus honest.

Throttle: `DIARY_THROTTLE_SECONDS` between DMs per (player, hero). The
DB stores `owned_heroes.last_diary_at` (unix); the caller checks
`should_send(last_at, now)` before deciding to DM, then writes the new
timestamp.

No LLM: every line is human-written. Per-hero voice overrides land in
v2 — for now every hero shares the corpus and personality comes from
in-line {hero}-aware phrasing.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Literal

EventType = Literal["hunt_kill", "hunt_chip", "gather_claim", "level_up"]
EVENT_TYPES: tuple[EventType, ...] = (
    "hunt_kill",
    "hunt_chip",
    "gather_claim",
    "level_up",
)

# One per hero per hour. Tuned so a multi-hit grind on the same mob
# doesn't bury the player in DMs.
DIARY_THROTTLE_SECONDS = 60 * 60

DIARY_CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "diary_lines.json"


def load_corpus(path: Path = DIARY_CORPUS_PATH) -> dict[str, list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    missing = [ev for ev in EVENT_TYPES if ev not in raw]
    if missing:
        raise ValueError(f"Diary corpus missing event types: {missing}")
    for ev in EVENT_TYPES:
        if not isinstance(raw[ev], list) or not raw[ev]:
            raise ValueError(f"Diary corpus entry for {ev} must be a non-empty list")
    return raw


def should_send(last_at: int, now: int, throttle: int = DIARY_THROTTLE_SECONDS) -> bool:
    """True iff `throttle` seconds have passed since the last DM for this hero."""
    if last_at <= 0:
        return True
    return now - last_at >= throttle


def pick_line(
    event: EventType,
    context: dict[str, str | int],
    *,
    rng: random.Random | None = None,
    corpus: dict[str, list[str]] | None = None,
) -> str:
    """Pick a random line for the event and render it against `context`.

    Raises KeyError if the template references a placeholder absent from
    `context` — surfaces corpus authoring bugs in tests instead of in
    user-facing DMs.
    """
    if event not in EVENT_TYPES:
        raise ValueError(f"Unknown diary event: {event!r}")
    corpus = corpus if corpus is not None else load_corpus()
    r = rng or random
    template = r.choice(corpus[event])
    return template.format(**context)
