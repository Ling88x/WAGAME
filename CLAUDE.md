# Witch Arcana RPG — Discord bot

> Read this file in full before doing anything else. You have **no memory**
> of prior sessions; everything you need is here. The user expects you to
> ask questions, not guess.

## What this project is

A Discord-native idle/RPG bot inspired by the mobile MMO **Witch Arcana**
(ATA Studios — same studio as *Kingdoms of Heckfire*; WA is the spiritual
successor). Players summon heroes ("witches"), upgrade them via research,
gather resources on a timed loop, and run raids. Long-term roadmap includes
PvP between players.

This is a **separate codebase** from `wahelper` (the hourly-quest helper bot
for the same game). They share the user, the source material, and the UX
preferences below — and nothing else. Do not reach into `wahelper`'s domain
from here, and do not duplicate its features.

## Source of truth for the game

- Official help / wiki: https://athinkingape.helpshift.com/hc/en/7-witch-arcana---magic-school/
- Hero database (community, comprehensive — names, rarity, element, terrain
  affinity, bonuses, lore): https://kohqs.com/wa/heroes

Use `WebFetch` on specific pages when you need official names, mechanics,
hero rosters, or balance data. **Do not invent game lore.** When the wiki is
silent, ask the user — they play the game and will tell you.

## Confirmed scope (from user)

These are the pillars the user has signed off on. Mechanics inside each one
are intentionally underspecified — you must clarify before implementing.

1. **Combat** — modelled loosely on in-game WA combat. The user will explain
   the in-game rules when you start working on this. **Ask first.**
2. **Hero collection — random shard rolls.** Players spend gems to summon
   unlock shards for specific heroes; 100 shards unlocks a hero, pity caps
   bad luck. Rarity tiers gate summon costs.
3. **Research** — XP / resource sink that boosts damage and unlocks things.
   Probably a tech tree. Shape and pacing — to be defined.
4. **Solo raids** — PvE bosses. Eventually PvP raids between players;
   defer until solo PvE feels good.
5. **XP / progression** — earned through play, fuels research and other
   upgrades. Sources and sinks — to be defined.
6. **Gathering** — start a timed action, claim later. Resources: gold,
   food, wood, plus event items in the future. Cooldown, slot count and
   resource costs — to be defined.
7. **Addictive-bot conveniences** — daily login rewards, streaks, leader-
   boards, idle income, etc. **Ask which ones the user wants** before
   building.

## What you must do FIRST in any new session

1. Read this file.
2. Greet the user briefly and confirm the system you're going to work on
   (e.g. "let's do gathering today?"). The user will pick the next slice.
3. **Ask clarifying questions before writing code.** The design is loose
   on purpose. The user has explicitly asked: *"dopisz do briefu aby
   dopytywał mnie o więcej szczegółów"* — they want you to interrogate
   the requirements rather than ship a default implementation.
4. Only after the design for that slice is pinned down, scaffold code.
5. Persist state, write tests where it pays off, and commit incrementally.

A non-exhaustive list of questions that must be answered before each
respective system is built — work through them with the user, don't
assume:

**Tech foundations (ask once, early):**
- Bot framework: `discord.py` 2.x (recommended, matches `wahelper`)?
- Data store: `aiosqlite` with versioned migrations (recommended)?
- Hosting / deployment target: same machine as `wahelper`? Separate token?
- Python version target?
- Persistence model: per-user globally, or per-guild silos? Cross-server
  trade/PvP only makes sense in a global model.
- Logging / observability needs?

**Heroes & summoning:**
- Source for hero list: scrape https://kohqs.com/wa/heroes, WebFetch the
  official wiki, or user-provided seed file? (kohqs has the cleanest
  structured data — name, rarity, element, terrain, bonuses.)
- How many heroes at launch? Rarities?
- Summon currency — gathered, daily-given, or paid (in-bot virtual)?
- Pity system / soft-pity / spark?
- Duplicate handling: shards / fodder / star-up?
- Element / class affinities mirror the in-game ones — confirm list.

**Combat:**
- Turn-based, auto-battler, or real-time tick?
- Element triangle? Rock-paper-scissors counters?
- Team size? Formation rules?
- Stats: HP / ATK / DEF / SPD / CRIT — same shape as the game?
- Active abilities, passives, ultimates?
- Status effects?
- Where do we need to render battle output — a single embed updated tick
  by tick, a battle log, or just a result screen?

**Research:**
- Tree shape: linear, branching, or grid?
- What does each node unlock — flat damage %, new ability, new gather
  slot?
- Cost curve: gold? XP? hero shards? time?
- Concurrent research limits, queueing, speedups?

**Gathering:**
- How many slots per player at start? Upgradable?
- Cooldown ranges per resource?
- Variable yields, RNG bonuses, crit drops?
- What does each resource buy: gold (research), food (heal/feed heroes?),
  wood (buildings? troops?)?
- Event items: how introduced — admin command, scheduled events?

**Raids:**
- One-shot bosses or persistent HP across the day?
- Cooldown / energy gating?
- Reward tables tied to damage dealt, kill, or attendance?
- Solo-only at v1, with PvP raid layer added later — confirmed.

**Profile / progression:**
- Player level vs hero level — separate?
- Account-wide stat boosts vs per-hero?
- Daily reset hour: same as in-game (21:00 UTC, see `wahelper`)?
- Streaks: how forgiving? Grace days? Streak-freeze items?

**UX / surface:**
- Slash-command-only, or also context-menus, message commands?
- One central `/wa` command with subcommands, or one command per system?
- Should the bot work in DMs?
- Are there public channel surfaces (e.g. raid announcements, leader-
  boards posted to a configured channel)?

## User preferences (carried over from `wahelper` — apply by default)

The user is the same person across both projects. They have strong UX
opinions; assume these unless told otherwise.

- **No noise in channels.** Avoid bots that spam many messages. Prefer a
  single rich embed that gets edited in place. The `wahelper` bot was
  rebuilt from 25 messages to 2 because the original was visually loud.
- **Persistent UI** — every panel is one message; interactions happen via
  Buttons and Selects with stable `custom_id`s; views are re-registered
  in `cog_load` via `bot.add_view(...)` so they survive restarts.
- **Edit in place > delete + recreate.** Persist message IDs in your DB
  and reuse them on restart. Never purge the channel as a side effect.
- **Ephemeral by default for personal data** — your inventory, your
  alerts, your stats: ephemeral interaction responses. Public surfaces
  are for shared state only.
- **Buttons over reactions.** Always.
- **Don't pre-build.** No speculative features, no "just-in-case"
  abstractions, no scaffolding for hypothetical mechanics. The user
  explicitly disliked this in `wahelper`.
- **Polish chat, English code/comments/commits** — the user converses in
  Polish; write code in English.
- **Don't gratuitously add emoji** in commit messages or prose to the
  user. Emoji are fine and welcome inside user-facing UI text.
- **Confirm before destructive or shared-state actions** — DB migrations
  with data loss, force-pushes, deletions. The user values being asked.
- **Don't keep updating this CLAUDE.md without asking first.** Treat it
  as a contract; revisions are a discussion, not a side effect.

## Recommended architecture (sketch — confirm with user)

Treat as a default to be approved or replaced — the user may want some-
thing else.

- `discord.py` 2.x; one cog per system (`gather`, `summon`, `combat`,
  `research`, `raid`, `profile`, `daily`, `admin`).
- `aiosqlite` for persistence. Single DB file per bot instance. Schema
  versioned via a `migrations/` directory; migrations applied on startup.
- Token + config in `.env` via `python-dotenv`. `.env.example` checked
  in; `.env` gitignored.
- Each cog exposes one slash command with subcommands (e.g. `/gather
  start`, `/gather claim`, `/gather status`).
- "Control panel" pattern: `/<system> panel` posts a single embed +
  view that the user can pin. State changes always go through the DB;
  the view re-renders from the DB.
- Tasks (`discord.ext.tasks`) for periodic ticks (gather completion
  checks, daily reset, raid windows). Watchdog + back-off retry like
  in `wahelper`.

## What this project is NOT

- Not a port of `wahelper`. Don't add hourly-quest features here. If the
  user mentions "the bot", clarify which one — they run both.
- Not an in-game scraper. We don't have an API to the game.
- Not a multi-server economy at v1. Cross-server PvP/trade is opt-in
  and comes after solo gameplay feels good.

## Useful pointers

- Sibling repo: https://github.com/Ling88x/wahelper (`hourly` cog there is
  worth reading for style: persistent views, edit-in-place message
  tracking, smoke-test on restart, watchdog + before_loop pattern).
- WA wiki: https://athinkingape.helpshift.com/hc/en/7-witch-arcana---magic-school/
- Hero database: https://kohqs.com/wa/heroes — full roster with rarity,
  element, terrain affinity and bonus lists. Use as the canonical source
  when seeding the summon pool.
- The user is `Ling88x` on GitHub.

## Open with the user every new session

> "Hi — I've read CLAUDE.md. Which system are we touching today: gathering,
> summoning, combat, research, raids, or progression? And before I start,
> can you walk me through the rules / what 'good' looks like for that slice?"

Then ask the relevant block of questions from the section above, agree on
scope, and only then code.
