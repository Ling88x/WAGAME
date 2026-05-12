# Witch Arcana — Discord RPG bot

A Discord-native idle/RPG companion bot for the mobile MMO **Witch Arcana**
(by ATA Studios; spiritual successor to *Kingdoms of Heckfire*). Players
collect heroes, research upgrades, gather resources and fight raids.

> **Brief & contract for any AI agent working on this repo: see [`CLAUDE.md`](CLAUDE.md).**
> It defines scope, design constraints, what to ask the user before writing code,
> and the relationship to the sibling project `wahelper` (hourly-quest helper).

Status: **heroes catalog seeded — `/profile`, `/heroes`, `/hero`, `/admin grant{,-hero,-reset}` work; gathering / gacha / combat still TBD.**

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
- `/admin grant user:<@user> gold:<n> food:<n> wood:<n>` — owner only.
- `/admin reset user:<@user>` — owner only; wipes the row.
- `/admin grant-hero codename:<slug> user:<@user> level:<n>` — owner only;
  grants a hero from the catalog. Repeated grants of the same hero increment
  `dupes_pending` instead of stacking, so the gacha PR can decide what to do
  with duplicates (shards / star-up / dust).

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
  heroes_data.py            seed loader + sync_heroes()
  cogs/                     discord.py cogs (one per system, eventually)
  game/                     pure logic — testable without Discord
  data/                     static seeds (heroes.json, …)
  tools/                    scripts (e.g. scrape_kohqs)
migrations/                 NNN_name.sql, applied in order
tests/                      pytest
```
