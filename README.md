# Witch Arcana — Discord RPG bot

A Discord-native idle/RPG companion bot for the mobile MMO **Witch Arcana**
(by ATA Studios; spiritual successor to *Kingdoms of Heckfire*). Players
collect heroes, research upgrades, gather resources and fight raids.

> **Brief & contract for any AI agent working on this repo: see [`CLAUDE.md`](CLAUDE.md).**
> It defines scope, design constraints, what to ask the user before writing code,
> and the relationship to the sibling project `wahelper` (hourly-quest helper).

Status: **Summoning Gate live — T1 troops trainable, one queue at a time, food-only cost. Higher tiers + queue scaling come with the research PR. See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the full plan.**

## Stack

- Python 3.10+, [`discord.py`](https://github.com/Rapptz/discord.py) 2.4+
- SQLite via `aiosqlite`, single file at `data/wagame.db`
- Hand-rolled migration runner — `migrations/NNN_name.sql`, applied on startup, version tracked in `schema_version`
- `python-dotenv` for config; `ruff` + `pytest` for dev

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

cp .env.example .env
# edit .env, fill DISCORD_TOKEN

python bot.py
```

The first run creates `data/wagame.db` and applies the initial migration.
Logs land in `data/wagame.log` (rotating) and stdout.

## Available commands

- `/profile` — your level, march capacity, and resources (ephemeral).
- `/heroes` — list the heroes you own (ephemeral).
- `/hero codename:<slug>` — full info on a hero from the catalog (ephemeral).
- `/gather` — ephemeral panel with five buttons (Gold / Food / Wood / Claim
  Ready / Refresh). Each march takes 30 min and produces a fixed-base yield
  plus a small chance of a x2 crit. Capacity starts at 2 marches and tops out
  at 6 (research will unlock the extra slots).
- `/summon` — Summoning Gate panel. Pick a troop from the dropdown, then
  Train 1 / 10 / 50 / Max. One batch at a time; troops auto-arrive in your
  city when the timer ends. Cost is food only. Tiers beyond T1 are gated
  behind research.
- `/army` — list the troops in your city, grouped by tier (ephemeral).
- `/admin grant user:<@user> gold:<n> food:<n> wood:<n>` — owner only.
- `/admin reset user:<@user>` — owner only; wipes the row.
- `/admin grant-hero codename:<slug> user:<@user> level:<n>` — owner only;
  grants a hero from the catalog. Repeated grants of the same hero increment
  `dupes_pending` instead of stacking, so the gacha PR can decide what to do
  with duplicates (shards / star-up / dust).
- `/admin unlock-tier tier:<1-4> user:<@user>` — owner only; raise a
  player's max trainable troop tier (research PR will replace this).
- `/admin set-queue-cap cap:<n> user:<@user>` — owner only.
- `/admin set-train-speed percent:<0-99> user:<@user>` — owner only.

## Heroes catalog

The roster lives in [`wagame/data/heroes.json`](wagame/data/heroes.json) and is
synced into the `heroes` table on every bot startup (idempotent upsert by
`codename`). The shipped seed only contains a couple of placeholders derived
from screenshots — populate the full roster on the VPS:

```bash
python -m wagame.tools.scrape_kohqs            # writes heroes.json
python -m wagame.tools.scrape_kohqs --dry      # preview only
python -m wagame.tools.scrape_kohqs --raw > p  # dump raw HTML for tweaking
```

The scraper is best-effort (HTML parser, no extra deps); when kohqs.com changes
its DOM, `_HeroBlockParser` / `_extract_heroes` in `wagame/tools/scrape_kohqs.py`
need a small adjustment.

## Layout

```
bot.py                      entrypoint
wagame/
  config.py                 .env loader
  db.py                     async SQLite + migration runner
  heroes_data.py            heroes seed loader + sync_heroes()
  troops_data.py            troops seed loader + sync_troops()
  cogs/                     discord.py cogs (one per system, eventually)
  game/                     pure logic — testable without Discord
  data/                     static seeds (heroes.json, …)
  tools/                    scripts (e.g. scrape_kohqs)
migrations/                 NNN_name.sql, applied in order
docs/                       roadmap, design notes
tests/                      pytest
```
