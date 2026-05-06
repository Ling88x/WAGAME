"""Bot entrypoint."""

from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

import discord
from discord.ext import commands

from wagame.config import REPO_ROOT, Config
from wagame.db import Database

INITIAL_COGS = (
    "wagame.cogs.profile",
    "wagame.cogs.admin",
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
        for ext in INITIAL_COGS:
            await self.load_extension(ext)
        await self.tree.sync()
        logging.getLogger(__name__).info("Slash commands synced.")

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
