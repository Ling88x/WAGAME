# Witch Arcana — Discord RPG bot

A Discord-native idle/RPG companion bot for the mobile MMO **Witch Arcana**
(by ATA Studios; spiritual successor to *Kingdoms of Heckfire*). Players
collect heroes, research upgrades, gather resources and fight raids.

> **Brief & contract for any AI agent working on this repo: see [`CLAUDE.md`](CLAUDE.md).**
> It defines scope, design constraints, what to ask the user before writing code,
> and the relationship to the sibling project `wahelper` (hourly-quest helper).

Status: **foundation scaffolded — `/profile`, `/admin grant`, `/admin reset` work, no gameplay yet.**

## Stack

- Python 3.11+, [`discord.py`](https://github.com/Rapptz/discord.py) 2.4+
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
- `/admin grant user:<@user> gold:<n> food:<n> wood:<n>` — owner only.
- `/admin reset user:<@user>` — owner only; wipes the row.

## Layout

```
bot.py                      entrypoint
wagame/
  config.py                 .env loader
  db.py                     async SQLite + migration runner
  cogs/                     discord.py cogs (one per system, eventually)
  game/                     pure logic — testable without Discord
migrations/                 NNN_name.sql, applied in order
tests/                      pytest
```
