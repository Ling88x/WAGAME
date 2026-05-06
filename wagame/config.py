"""Runtime configuration loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    discord_token: str
    db_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> Config:
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
            )

        db_path_str = os.getenv("WAGAME_DB_PATH", "data/wagame.db").strip()
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            db_path = REPO_ROOT / db_path

        log_level = os.getenv("WAGAME_LOG_LEVEL", "INFO").strip().upper()

        return cls(discord_token=token, db_path=db_path, log_level=log_level)
