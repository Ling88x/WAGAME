# Witch Arcana bot — roadmap & backlog

Living document. Pinned design decisions go under "PRs"; loose ideas / things
the user mentioned in passing go under "Backlog" — we revisit them when
their context comes up. **Do not silently rewrite this file**; treat each
edit as a discussion with the user.

---

## PR status

| PR | System            | Status     | Notes                                          |
|----|-------------------|------------|------------------------------------------------|
| #1 | Foundations       | ✅ shipped | discord.py + aiosqlite scaffold, `/profile`, `/admin grant`/`reset` |
| #2 | Heroes catalog    | ✅ shipped | 40 heroes seeded from kohqs.com; `/heroes`, `/hero`, `/admin grant-hero` |
| #3 | Gathering         | ✅ shipped | 2 starting marches (cap 6 via future research), 30 min, fixed yield + 5% crit ×2, gold leaner than food/wood |
| #4 | Summoning Gate    | ✅ shipped | Troop training, 1 queue, food-only cost, T1 only at start (research unlocks T2–T4) |
| #5 | Research tree     | ✅ shipped | 8 nodes across Economy / Military / Logistics, 1 slot, gold-only cost, auto-apply on completion |
| #6 | Hero leveling     | ✅ shipped | XP curve `100×L^1.5`, +5% ATK / +2% march-speed per level, cap 30 (banked XP at cap), `/admin grant-hero-xp` |
| #7 | Hunt (`/hunt`)    | ✅ shipped | 12-level tenebrals (HP 750 → 100B), energy cap 500 / regen 1pt-30s, faux-timer 15–90s, persistent mob HP, daily quota 10 kills |
| #8 | Gather refactor   | ⏳ pending | Add hero slot, march time tied to hero march_speed_pct + research |
| #9 | Combat            | ⏳ pending | Auto-battler loosely based on in-game WA; element triangle TBD; troops + commander hero per side |
| #10 | Raids            | ⏳ pending | Solo PvE first (R1–R6 → void vesps → dominion boss). Energy gated. PvP raids later |
| #11 | Daily / progression | ⏳ pending | Login rewards, streaks, account-wide XP — defer until PvE feels good |

---

## Confirmed design rules

These were pinned by the user; treat as contract.

- **Per-user-global model.** Data keyed by `discord_user_id`, not per-guild.
- **Single-message panels, edit in place.** Personal panels (`/profile`,
  `/gather`, `/train`, `/army`, `/summon`) are ephemeral so only the invoker sees them.
  Buttons over reactions, no channel spam.
- **Polish chat / English code.** Commits, code, comments, docs in English.
- **Ask before risky / shared-state actions.** Don't force-push, don't drop
  data without confirmation. Don't rewrite `CLAUDE.md` or this file silently.
- **Don't pre-build.** Each PR ships only what the slice needs. No
  speculative abstractions.

---

## Numbers snapshot (PR #3 — gathering)

| Resource | Base yield (per 30 min) | Notes |
|----------|--------------------------|-------|
| Gold     | 400–800                  | Slightly leaner ("żyły złota") |
| Food     | 500–1000                 | |
| Wood     | 500–1000                 | |

- Crit: 5% chance, ×2 yield. Frozen at start, deterministic on claim.
- Marches: 2 at start, cap 6 (research-gated).
- Yield is decoupled from troops for now; the long-term plan ties march
  duration to troop march speed and yield to `count × carry_cap` (see
  backlog → "Gather refactor").

---

## Numbers snapshot (PR #5 — Research)

8 nodes, one in-flight job per player, gold-only cost. Each completed
level adds `effect_per_level` to a single player column.

| Track     | Node              | Max lvl | Per level             | Effect column                | Prereq                |
|-----------|-------------------|---------|------------------------|------------------------------|------------------------|
| Economy   | Gather Yield      | 5       | +5% yield              | gather_yield_pct             | —                      |
| Economy   | Gather Speed      | 5       | −3% gather duration    | gather_speed_pct             | —                      |
| Economy   | Summoning Queue   | 5       | +50 batch cap          | training_queue_cap           | —                      |
| Economy   | Summoning Speed   | 5       | +3% training speed     | training_speed_boost_pct     | Summoning Queue Lv 2  |
| Military  | Higher Tier Summons | 3     | +1 troop tier (T2–T4)  | unlocked_tier                | —                      |
| Military  | Troop Attack      | 5       | +5% combat ATK         | troop_attack_pct             | Higher Tier Lv 1      |
| Military  | Troop HP          | 5       | +5% combat HP          | troop_hp_pct                 | Higher Tier Lv 1      |
| Logistics | March Slots       | 4       | +1 march slot (2 → 6)  | march_capacity               | —                      |

Gold costs and durations escalate roughly 2× per level; full tables live
in `wagame/research_data.py`.

---

## Numbers snapshot (PR #4 — Summoning Gate)

Rounded values from the in-game screens. Game-side numbers are gigantic
(L25 queue = 42k, L30 = 192k) — we scale down for bot scope.

### Troop roster (8 units, 4 tiers)

| Tier | Codename | Name    | Role     | Spd | Carry | ATK | HP  | Role bonus |
|------|----------|---------|----------|-----|-------|-----|-----|------------|
| T1   | catsith  | Catsith | —        | 40  | 1.0   | 60  | 55  | —          |
| T2   | gryphon  | Gryphon | —        | 40  | 1.0   | 140 | 140 | —          |
| T3   | dratsie  | Dratsie | monster  | 72  | 3.0   | 76  | 199 | +100 vs monsters |
| T3   | pangolin | Pangolin| defender | 64  | 1.0   | 76  | 199 | +100 defending |
| T3   | musjay   | Musjay  | player   | 71  | 1.42  | 76  | 199 | +100 vs players |
| T4   | dawon    | Dawon   | monster  | 113 | 3.0   | 200 | 500 | +150 vs monsters |
| T4   | shishi   | Shishi  | defender | 101 | 1.0   | 200 | 500 | +150 defending |
| T4   | kelpie   | Kelpie  | player   | 119 | 1.42  | 200 | 500 | +150 vs players |

### Training economics (initial proposal, balance pass later)

| Tier | Food / unit | Time / unit (no boost) |
|------|-------------|--------------------------|
| T1   | 15          | 30 s                     |
| T2   | 50          | 2 min                    |
| T3   | 120         | 10 min                   |
| T4   | 300         | 1 h                      |

- **Queue cap (batch size):** 50 troops at start. Research will raise it.
- **Speed boost:** 0% at start. Research adds %.
- **Slots:** 1 training slot. Premium 2nd slot is a backlog item.
- **Cost:** food only.
- **Auto-claim:** when the timer ends, troops auto-arrive in the city.
  Player must manually start the next batch — no auto-requeue (deliberate
  friction, by user's call).
- **Tier gating:** T1 unlocked at start. T2–T4 unlocked via research nodes.

---

## Backlog — captured from chat, revisit later

Each item is something the user has mentioned but we agreed to defer.
When the relevant PR comes up, raise the item and ask whether to fold it in.

### Soon (next 1–2 PRs)
- **Combat (PR #6).** Loosely Heckfire-like auto-battler. Hero commander +
  troops on each side. Element triangle TBD. Open question: rendering — tick
  log vs result screen. Reads `troop_attack_pct` / `troop_hp_pct` already
  populated by research.
- **Wood needs a sink.** Research is gold-only by user's call. Food is
  consumed by Training Grounds, gold by research. Wood is still produced by
  gather but consumed by nothing — likely role for future buildings system
  or a second research currency tier. Revisit when buildings come up.

### Numbers snapshot (PR #6 — Hero leveling)

- Level cap: 30 at v1. Future research raises it (backlog node "Hero Max
  Level"). XP banked past the cap is consumed on the next cap raise.
- XP curve: `xp_to_next(L) = floor(100 * L^1.5)`. Lv1→2 = 100, Lv29→30 ≈
  15.6k. Total Lv1→30 ≈ 150k XP.
- Stats per level (additive):
  - +5% effective ATK over the rarity baseline.
  - +2% hero march-speed bonus (stacks on top of research).
- Rarity baseline ATK: mythic 500 · legendary 300 · epic 200 · rare 120
  · uncommon 70 · common 40. Derived from rarity until per-hero stats
  land — heroes.json doesn't carry `atk_base` yet.
- Excess shards (`hero_shards.count` ≥ 100) still flow into
  `owned_heroes.dupes_pending`; consuming dupes for direct level-ups
  stays in the backlog ("Hero level-up" → shard sinks).

### Numbers snapshot (PR #7 — Tenebral Hunt)

- 12 levels copied 1:1 from `docs/tenebral-data.md` (HP 750 → 100B, energy
  25 → 139). Default players one-shot lv1, grind lv2–3 for weeks, lv6+
  is gated on stacked research + hero levels.
- Energy: cap 500, regen 1pt / 30s (lazy formula on
  `players.energy_updated_at`). Full pool refills in ~4h 10m.
- March power: `(hero_atk + Σ troop.atk × count) × (1 + troop_attack_pct)`.
  Auto-fills with every owned troop — no per-stack slider yet.
- Min-power gate per level = `tenebral.hp / 200` (worst case ~200 hits).
  Attack disabled below the gate.
- Damage roll: deterministic in v1 (no crit / variance) — RNG layer in
  the balance pass.
- Faux-timer: `base × (1 − hero_march_speed − research_march_speed)`,
  clamped to [15s, 90s].
- Mob spawns persist between attacks (`tenebral_spawns`); kill despawns
  + awards RSS (split evenly across gold/food/wood) + Hero XP to the
  march hero (cascades through `apply_xp_gain`).
- Daily quota: 10 kills, reset 21:00 UTC, reward 50 gems + 100k each of
  gold/food/wood. Tracked in `hunt_daily_progress` keyed on reset day.
- Resilience: `cog_load` drains any unresolved `hunt_marches` from a
  prior boot so energy never silently burns.

### Medium term
- **Gather refactor — troops as gatherers.** Description in-game:
  *"Summon troops for defense, offense, and resource gathering."* Eventually
  a march to a node ties up N troops; duration = f(distance, march_speed);
  yield = `count × carry_cap`. Heroes give march-speed % bonuses on top.
  Until then, `/gather` stays standalone with fixed yield/time.
- **Marches starting slow, sped up later.** User's call: early game marches
  feel sluggish; research + heroes shave time. Bake this into the gather
  refactor.
- **Buildings system.** Training Grounds, Great Hall, helper buildings — each
  with levels and upgrade costs. **Explicitly deferred** by user to avoid
  game getting too heavy. Until then, queue cap / speed boost / march cap
  live as per-player columns scaled by research.
- **Hero level-up.** Once unlocked, shards keep accumulating in
  `hero_shards.count`. Need a cost curve + stat scaling to consume them
  beyond the 100-shard unlock floor. Currently they just sit there.
- **Heroes element / house fields.** Currently `NULL` in the catalog
  (the list page doesn't carry them). Either scrape the per-hero detail
  pages on kohqs.com or fill manually — needed before element-based
  combat math is real.

### Long term
- **Raids — R1–R6 → void vesps → dominion boss.** Six difficulty tiers
  feeding into bigger fights. Solo first, PvP raid layer later.
- **Energy systems.** Separate energies for marches vs raids (per user's
  earlier note).
- **Premium 2nd training slot.** Cosmetic / vanity tier, no spec yet.
- **Daily login rewards & streaks.** With grace days / streak-freeze items.
- **Leaderboards.** Format / scope TBD.
- **Account-wide XP vs per-hero level.** Confirmed both exist; relationship
  to research and combat power — TBD.
- **PvP between players.** Trade, raids, arena — anything cross-server
  needs the per-user-global model already in place.
- **Full balance pass.** All cost / time / yield numbers will be tuned in
  one sweep once the major systems are wired together. Until then we use
  educated placeholders.

---

## Operations notes (for future me)

- VPS ships Python 3.10.12 — `pyproject.toml` is pinned to `>=3.10` for
  that reason. Don't bump without checking.
- Slash command sync: global takes up to ~1h to propagate. Set
  `WAGAME_GUILD_ID` in `.env` during dev for instant guild-scoped sync;
  the bot also wipes global commands so they don't show up twice.
- The DB lives at `data/wagame.db` (gitignored). Migrations are append-only
  under `migrations/NNN_name.sql` and applied on startup.
- Hero seed is committed at `wagame/data/heroes.json` and re-synced into
  the `heroes` table on every boot.
