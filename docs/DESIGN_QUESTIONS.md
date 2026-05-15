# Design questions per subsystem

> Read the relevant block before starting work on a subsystem. These are
> the things the user has explicitly asked to be asked about — don't ship
> defaults.

## Tech foundations (ask once, early)

- Bot framework: `discord.py` 2.x (recommended, matches `wahelper`)?
- Data store: `aiosqlite` with versioned migrations (recommended)?
- Hosting / deployment target: same machine as `wahelper`? Separate token?
- Python version target?
- Persistence model: per-user globally, or per-guild silos? Cross-server
  trade/PvP only makes sense in a global model.
- Logging / observability needs?

## Heroes & summoning

- Source for hero list: scrape https://kohqs.com/wa/heroes, WebFetch the
  official wiki, or user-provided seed file? (kohqs has the cleanest
  structured data — name, rarity, element, terrain, bonuses.)
- How many heroes at launch? Rarities?
- Summon currency — gathered, daily-given, or paid (in-bot virtual)?
- Pity system / soft-pity / spark?
- Duplicate handling: shards / fodder / star-up?
- Element / class affinities mirror the in-game ones — confirm list.

## Combat

- Turn-based, auto-battler, or real-time tick?
- Element triangle? Rock-paper-scissors counters?
- Team size? Formation rules?
- Stats: HP / ATK / DEF / SPD / CRIT — same shape as the game?
- Active abilities, passives, ultimates?
- Status effects?
- Where do we need to render battle output — a single embed updated tick
  by tick, a battle log, or just a result screen?

## Research

- Tree shape: linear, branching, or grid?
- What does each node unlock — flat damage %, new ability, new gather slot?
- Cost curve: gold? XP? hero shards? time?
- Concurrent research limits, queueing, speedups?

## Gathering

- How many slots per player at start? Upgradable?
- Cooldown ranges per resource?
- Variable yields, RNG bonuses, crit drops?
- What does each resource buy: gold (research), food (heal/feed heroes?),
  wood (buildings? troops?)?
- Event items: how introduced — admin command, scheduled events?

## Raids

- One-shot bosses or persistent HP across the day?
- Cooldown / energy gating?
- Reward tables tied to damage dealt, kill, or attendance?
- Solo-only at v1, with PvP raid layer added later — confirmed.

## Profile / progression

- Player level vs hero level — separate?
- Account-wide stat boosts vs per-hero?
- Daily reset hour: same as in-game (21:00 UTC, see `wahelper`)?
- Streaks: how forgiving? Grace days? Streak-freeze items?

## UX / surface

- Slash-command-only, or also context-menus, message commands?
- One central `/wa` command with subcommands, or one command per system?
- Should the bot work in DMs?
- Are there public channel surfaces (e.g. raid announcements, leader-
  boards posted to a configured channel)?
