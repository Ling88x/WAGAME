"""Bot entrypoint."""

from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands

from wagame.config import REPO_ROOT, Config
from wagame.db import Database
from wagame.heroes_data import sync_heroes
from wagame.troops_data import sync_troops

INITIAL_COGS = (
    "wagame.cogs.profile",
    "wagame.cogs.admin",
    "wagame.cogs.heroes",
    "wagame.cogs.gather",
    "wagame.cogs.train",
    "wagame.cogs.research",
    "wagame.cogs.summon",
    "wagame.cogs.hunt",
    "wagame.cogs.diary",
    "wagame.cogs.hub",
)


def _setup_logging(level: str) -> None:
    log_dir = REPO_ROOT / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    rotating = RotatingFileHandler(
        log_dir / "wagame.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(fmt)
    root.addHandler(rotating)


class WaBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!wa", intents=intents)
        self.config = config
        self.db = Database(config.db_path)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.db.migrate()
        await sync_heroes(self.db)
        await sync_troops(self.db)
        for ext in INITIAL_COGS:
            await self.load_extension(ext)

        log = logging.getLogger(__name__)
        if self.config.dev_guild_id is not None:
            # Guild-scoped sync propagates instantly — use during development.
            # Order matters: copy globals into the guild tree and sync there
            # FIRST, then wipe globals from Discord so commands don't appear
            # twice (once globally, once per guild).
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d slash command(s) to guild %d.",
                     len(synced), self.config.dev_guild_id)

            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            log.info("Cleared global slash commands (dev guild mode).")
        else:
            synced = await self.tree.sync()
            log.info("Synced %d slash command(s) globally (may take up to 1h to propagate).",
                     len(synced))

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def main() -> None:
    config = Config.from_env()
    _setup_logging(config.log_level)
    bot = WaBot(config)
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
