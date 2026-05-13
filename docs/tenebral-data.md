# Tenebral Hunt — raw in-game data

> Extracted from user-provided screenshots of all 12 tenebral levels +
> the Create March panel. Source of truth for balancing our `/hunt`
> reimplementation. **Numbers below are the in-game ground truth — our
> bot will scale them down (or up) to its own economy, not copy 1:1.**

## The 12 levels

| Lv | Boss          | Biome     | HP      | March (empty) | Energy | Notes                          |
|----|---------------|-----------|---------|---------------|--------|--------------------------------|
| 1  | Void Cervus   | Forest    | 750     | 8m 18s        | 25     |                                |
| 2  | Void Vespa    | Plains    | 30,000  | 8m 4s         | 35     |                                |
| 3  | Void Lupus    | Highlands | 450,000 | 7m 6s         | 65     |                                |
| 4  | Void Cervus   | Forest    | 3.6M    | 6m 26s        | 67     |                                |
| 5  | Void Cervus   | Forest    | 25M     | 4m 40s        | 73     |                                |
| 6  | Void Lupus    | Highlands | 88M     | 3m 40s        | 79     |                                |
| 7  | Void Lupus    | Highlands | 275M    | 3m 7s         | 88     |                                |
| 8  | Void Vespa    | Plains    | 900M    | 2m 32s        | 92     |                                |
| 9  | Void Lupus    | Highlands | 4B      | 2m 31s        | 97     |                                |
| 10 | Void Lupus    | Highlands | 13B     | 2m 28s        | 125    |                                |
| 11 | Void Cervus   | Forest    | 40B     | 2m 53s        | 132    |                                |
| 12 | Void Cervus   | Forest    | 100B    | —             | 139    | Locked: requires Tenebral Hunt research lvl 12 |

HP curve: ~40× from 1→2, ~15× 2→3, settles around 3× per level after lvl 5.
Total spread 750 → 100B = ~133 million ×.

Energy cost: 25 → 139 over 12 levels (~+10 per level, linear-ish).

## Three boss families

- **Void Cervus** — Forest biome (deer-like)
- **Void Vespa** — Plains biome (wasp-like)
- **Void Lupus** — Highlands biome (wolf-like)

The 12 levels rotate through these — biome appears tied to map location
on the world map, not strictly to level tier. Each family probably has
its own stat profile (the user has not yet confirmed counter rules).

## Create March panel — what the player picks

- **Power slider** — current march power (e.g. 188.5B empty → 1.2T full)
- **March Cap** — max troop count in this march (e.g. 451,415)
- **Carry** — RSS carry capacity (grows with troop count)
- **Ally Bonus** — global stat boost from clubmates (e.g. +532.3% on
  user's account)
- **Hero slots** — multiple heroes per march (user's screen shows 2)
- **Troop sliders** — per troop type, can mix. User's account picks Dawon
  (T4) only; lower tiers have 0.
- **March Time field** — collapses dramatically once troops + heroes are
  loaded (from "2m 53s" empty to "23s" with the full march). The drop is
  driven by hero passive speed bonuses + troop base speed + research.

## Hero Activity panel

- Shows ongoing marches with countdown ("Forest - Void Cervus [Lv11] —
  00:00:24")
- "All able marches: 1 / 1" — concurrent march cap (default 1, scales
  with research per the glossary).
- Fast-forward and recall buttons per march.

## What the user said about difficulty

> "moje konto ma już duże bonusy ale my musimy przerobić to aby było
> ciężko uderzyć każdy następny poziom tenebrali"

i.e. our bot's default player must struggle at each tier — don't copy
the user's late-account flat curve. Early levels = trivial; mid levels =
multi-hit grind; lvl 12 = endgame.

## Rewards from the boss panel

Each tenebral kill shows a "Potential Rewards" strip with:
- **Tier 1** shard (purple gem icon — visible at all levels in the
  screens)
- Gold / RSS icon
- Blue icon (likely energy pot / brew?)
- Red icon (likely combat speedup or item?)

User hasn't yet confirmed the exact drop table — this needs a follow-up.
