# Brief — concise (draft)

Status: **draft for review**, not the active contract. The active brief is
still `CLAUDE.md` at repo root. If you adopt this file, `CLAUDE.md` could
shrink to a pointer (open questions + link here) — but that's a discussion,
not a side effect.

## What this is

Discord-native idle/RPG bot inspired by **Witch Arcana** (ATA Studios).
Players summon heroes, upgrade via research, gather on a timer, run raids.
Long-term: PvP between players.

Separate codebase from `wahelper` (hourly-quest helper for the same game).
Do not cross domains.

## Sources of truth

- Official wiki: https://athinkingape.helpshift.com/hc/en/7-witch-arcana---magic-school/
- Hero database: https://kohqs.com/wa/heroes
- In-game jargon: `docs/glossary.md`
- PR ledger & backlog: `docs/ROADMAP.md`

Don't invent lore. When the wiki is silent, ask.

## User preferences (hard rules)

- **No channel noise.** One rich embed, edited in place. Persist message IDs.
- **Persistent UI.** Buttons + Selects with stable `custom_id`; re-register
  views in `cog_load` via `bot.add_view(...)`.
- **Ephemeral by default** for personal data.
- **Buttons over reactions.** Always.
- **No speculative scaffolding.** Don't pre-build for hypothetical mechanics.
- **Polish chat, English code/comments/commits.**
- **No gratuitous emoji** in commits or prose. Emoji fine inside UI text.
- **Confirm before destructive / shared-state actions** (migrations with
  data loss, force-push, deletions).
- **Don't silently rewrite `CLAUDE.md` or `ROADMAP.md`.** Treat both as
  contracts.

## Architecture (current, not aspirational)

- `discord.py` 2.x, one cog per system.
- `aiosqlite`, single DB file, versioned migrations in `migrations/`,
  applied on startup.
- `.env` via `python-dotenv`; `.env.example` checked in, `.env` gitignored.
- One slash command per system with subcommands (`/gather start`, etc.).
- "Campus" hub pattern: `/wa` posts a single embed + buttons routing to
  each subpanel; state always read from DB, view re-renders.
- `discord.ext.tasks` for periodic ticks; watchdog + back-off retry.

## Session opener

> "Hi — I've read the brief. Which system today? And before I start, what
> does 'good' look like for that slice?"

Then interrogate the relevant question block in `CLAUDE.md` ("Heroes &
summoning", "Combat", "Research", "Gathering", "Raids", "Profile",
"UX / surface") before writing code. The user has explicitly asked to be
asked.

## What this is NOT

- Not a port of `wahelper`.
- Not an in-game scraper (no API).
- Not a multi-server economy at v1.

## Pointers

- `wahelper` sibling repo: https://github.com/Ling88x/wahelper — read its
  `hourly` cog for style (persistent views, edit-in-place, watchdog).
- The user is `Ling88x` on GitHub.
