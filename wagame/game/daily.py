"""Daily login bonus — auto-claimed on the player's first interaction
after the WA in-game reset boundary (21:00 UTC).

The "reset day" is the date of the most recent 21:00 UTC tick; a player
claims at most one bonus per reset day. `claim_daily_if_due` is
idempotent — calling it twice on the same day is a no-op.
"""

from __future__ import annotations

import datetime

from wagame.db import Database
from wagame.game.gacha import DAILY_GEM_BONUS

# In-game daily reset (also used by wahelper). Shifting `now - RESET_HOUR`
# and taking the date gives the label of the current reset day.
RESET_HOUR_UTC = 21


def current_reset_day(now: datetime.datetime | None = None) -> datetime.date:
    """Return the date string label of the current WA reset day (UTC)."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    shifted = now - datetime.timedelta(hours=RESET_HOUR_UTC)
    return shifted.date()


async def claim_daily_if_due(
    db: Database,
    user_id: int,
    now: datetime.datetime | None = None,
) -> int:
    """Award DAILY_GEM_BONUS if this player hasn't claimed today's reset.

    Returns the number of gems granted (0 if already claimed today).
    Safe to call on every interaction — repeated calls within the same
    reset day are a no-op.
    """
    today = current_reset_day(now).isoformat()
    async with db.conn.execute(
        "SELECT last_daily_claim_date FROM players WHERE discord_user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if row is None or row["last_daily_claim_date"] == today:
        return 0

    await db.conn.execute(
        """
        UPDATE players
        SET gems = gems + ?, last_daily_claim_date = ?
        WHERE discord_user_id = ?
        """,
        (DAILY_GEM_BONUS, today, user_id),
    )
    await db.conn.commit()
    return DAILY_GEM_BONUS
