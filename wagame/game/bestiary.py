"""Pure logic for the Bestiary — completion math, line rendering helpers.

Only `mob_kind = "tenebral"` is wired in v1; the kind column exists so
future families (furies, void vesps) can reuse the same table.

A "documented" entry is one with `encounter_count > 0` — i.e. the
player has at least attacked the mob, even if not killed it. Lines
display kill counts when slain, "encountered N times" when only
chipped, and "???" when untouched.
"""

from __future__ import annotations

from dataclasses import dataclass

# Total number of slots for the tenebral family — matches TENEBRAL_TABLE.
TENEBRAL_SLOT_COUNT = 12


@dataclass(frozen=True)
class BestiaryLine:
    level: int
    name: str
    slain: int
    encounters: int
    first_seen_at: int
    last_seen_at: int

    @property
    def documented(self) -> bool:
        return self.encounters > 0

    @property
    def slain_any(self) -> bool:
        return self.slain > 0


def completion_pct(documented: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(100 * documented / total)


def render_line(line: BestiaryLine) -> str:
    """One-line summary for the bestiary embed."""
    if not line.documented:
        return f"❓ Lv {line.level:>2}  ???"
    if line.slain_any:
        last = (
            f" · last kill <t:{line.last_seen_at}:R>"
            if line.last_seen_at
            else ""
        )
        return (
            f"✅ Lv {line.level:>2}  {line.name}  · "
            f"slain **{line.slain}**x ({line.encounters} encounters){last}"
        )
    return (
        f"👁 Lv {line.level:>2}  {line.name}  · "
        f"encountered {line.encounters}x but never slain"
    )
