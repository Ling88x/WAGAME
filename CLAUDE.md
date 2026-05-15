# Witch Arcana RPG — Discord bot

> Read this file in full. You have **no memory** of prior sessions. The
> user expects you to ask questions, not guess.

## What this is

Discord-native idle/RPG bot inspired by **Witch Arcana** (ATA Studios).
Players summon heroes ("witches"), upgrade via research, gather on a
timer, run raids. Long-term: PvP.

Separate codebase from `wahelper` (hourly-quest helper for the same
game). Don't cross domains, don't duplicate its features.

## Sources of truth

- Official wiki: https://athinkingape.helpshift.com/hc/en/7-witch-arcana---magic-school/
- Hero database: https://kohqs.com/wa/heroes (rarity, element, terrain, bonuses)
- In-game jargon: `docs/glossary.md`
- PR ledger & backlog: `docs/ROADMAP.md`
- Per-subsystem question blocks: `docs/DESIGN_QUESTIONS.md`

Use `WebFetch` for official names / mechanics. **Don't invent game lore.**
When the wiki is silent, ask.

## Confirmed scope (the seven pillars)

Mechanics inside each pillar are underspecified — clarify before coding.

1. **Combat** — loosely based on in-game WA combat. **Ask first.**
2. **Hero collection** — gem-funded summons drop shards; 100 shards
   unlocks a hero; pity caps bad luck. Rarity gates summon costs.
3. **Research** — XP / resource sink. Probably a tech tree.
4. **Solo raids** — PvE bosses. PvP raid layer comes later.
5. **XP / progression** — fuels research and upgrades.
6. **Gathering** — timed action, claim later. Gold, food, wood, + event
   items in the future.
7. **Addictive-bot conveniences** — daily logins, streaks, leaderboards,
   idle income. **Ask which ones** before building.

## Workflow every new session

1. Read this file.
2. Greet briefly and confirm which slice we're touching today.
3. **Read the relevant block in `docs/DESIGN_QUESTIONS.md` and ask
   clarifying questions before writing code.** The user has explicitly
   asked to be interrogated — *"dopisz do briefu aby dopytywał mnie o
   więcej szczegółów"*. Don't ship a default implementation.
4. Only after the slice is pinned down, scaffold code.
5. Persist state, test where it pays off, commit incrementally.

## Hard rules (the contract — apply by default)

- **No channel noise.** One rich embed, edited in place. Persist message
  IDs and reuse on restart. Never purge channels.
- **Persistent UI.** Buttons + Selects with stable `custom_id`; re-register
  views in `cog_load` via `bot.add_view(...)`.
- **Edit in place > delete + recreate.**
- **Ephemeral by default for personal data.** Public surfaces only for
  shared state.
- **Buttons over reactions.** Always.
- **No speculative scaffolding.** No "just-in-case" abstractions, no
  pre-builds for hypothetical mechanics.
- **Polish chat, English code/comments/commits.**
- **No gratuitous emoji** in commits or prose to the user. Emoji fine
  inside UI text.
- **Confirm before destructive / shared-state actions** — DB migrations
  with data loss, force-push, deletions.
- **Don't silently rewrite `CLAUDE.md` or `docs/ROADMAP.md`.** Both are
  contracts; revisions are a discussion.

## Architecture (current)

- `discord.py` 2.x, one cog per system.
- `aiosqlite`, single DB file, versioned migrations in `migrations/`,
  applied on startup.
- `.env` via `python-dotenv`; `.env.example` checked in, `.env` gitignored.
- One slash command per system with subcommands.
- Hub pattern: `/wa` (Campus) posts a single embed + buttons routing to
  subpanels; state read from DB; view re-renders.
- `discord.ext.tasks` for periodic ticks; watchdog + back-off retry.

## What this is NOT

- Not a port of `wahelper`. If the user says "the bot", clarify which.
- Not an in-game scraper. No API.
- Not a multi-server economy at v1.

## Pointers

- `wahelper` sibling repo: https://github.com/Ling88x/wahelper — read its
  `hourly` cog for style (persistent views, edit-in-place, watchdog).
- User on GitHub: `Ling88x`.

## Session opener

> "Hi — I've read CLAUDE.md. Which system today? Walk me through the
> rules / what 'good' looks like before I start."

Then consult `docs/DESIGN_QUESTIONS.md` for that subsystem.
