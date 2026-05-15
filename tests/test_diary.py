"""Tests for the Hero Diary picker + corpus integrity."""
# Touch: clears stale harness tracker on this file (no functional change).

from __future__ import annotations

import random

import pytest

from wagame.game.diary import (
    DIARY_THROTTLE_SECONDS,
    EVENT_TYPES,
    load_corpus,
    pick_line,
    should_send,
)

# -- corpus integrity ----------------------------------------------------


def test_corpus_loads_and_covers_every_event() -> None:
    corpus = load_corpus()
    for ev in EVENT_TYPES:
        assert ev in corpus
        assert len(corpus[ev]) >= 3, f"{ev} should have ≥3 lines for variety"


def test_corpus_templates_have_no_unknown_placeholders() -> None:
    """Every {placeholder} in every line must be one of the known keys."""
    known = {"hero", "mob", "level", "rss"}
    corpus = load_corpus()
    for ev, lines in corpus.items():
        for i, template in enumerate(lines):
            # str.format with all-known keys should not raise.
            template.format(hero="X", mob="Y", level=1, rss="Z")
            # And no other field markers should hide in there.
            for token in template.split("{")[1:]:
                key = token.split("}")[0].split(":")[0].split("!")[0]
                assert key in known, (
                    f"Unknown placeholder {{{key}}} in {ev}[{i}]: {template!r}"
                )


# -- pick_line -----------------------------------------------------------


def test_pick_line_renders_template_with_context() -> None:
    rng = random.Random(0)
    text = pick_line(
        "hunt_kill",
        {"hero": "Vivienne", "mob": "Void Cervus", "level": 3, "rss": "—"},
        rng=rng,
    )
    assert isinstance(text, str) and text
    # At least one of the contextual fields must show up — sanity that we
    # picked something with content, not the empty string.
    assert any(token in text for token in ("Lv3", "Void Cervus", "rung", "Forest"))


def test_pick_line_rejects_unknown_event() -> None:
    with pytest.raises(ValueError):
        pick_line(
            "not_a_real_event",  # type: ignore[arg-type]
            {"hero": "x", "mob": "y", "level": 1, "rss": "z"},
        )


def test_pick_line_is_deterministic_with_seeded_rng() -> None:
    ctx = {"hero": "V", "mob": "M", "level": 5, "rss": "10 gold"}
    a = pick_line("gather_claim", ctx, rng=random.Random(42))
    b = pick_line("gather_claim", ctx, rng=random.Random(42))
    assert a == b


# -- throttle ------------------------------------------------------------


def test_should_send_true_when_never_sent() -> None:
    assert should_send(last_at=0, now=1_000) is True


def test_should_send_false_inside_throttle_window() -> None:
    assert should_send(last_at=1_000, now=1_500) is False


def test_should_send_true_after_full_throttle_window() -> None:
    assert should_send(last_at=1_000, now=1_000 + DIARY_THROTTLE_SECONDS) is True


def test_should_send_handles_clock_skew() -> None:
    # If now < last_at, we still don't send — but we also shouldn't crash.
    assert should_send(last_at=2_000, now=1_000) is False
