-- Gems (premium currency) and per-hero unlock shards.
--
-- Existing players are seeded with 5000 gems (1 free legendary pull or
-- a handful of cheaper pulls). New players get the same via the column
-- DEFAULT — so this stays consistent on a fresh install.
--
-- `last_daily_claim_date` records the latest WA reset-day (21:00 UTC
-- boundary) at which the player auto-collected their +100 daily gems.
-- Claim happens on the player's first interaction after the boundary;
-- the value is the date of that day in YYYY-MM-DD form (UTC, shifted).
--
-- `hero_shards` is the per-player unlock progress per hero. `count`
-- accumulates without cap — once it hits 100 the hero auto-unlocks
-- (insert into owned_heroes) but shards keep growing for future level
-- upgrades. `pity_pulls` is the rolling number of pulls on this hero
-- since the last unlock; resets to 0 on unlock; hard pity top-up
-- triggers at PITY_PULL_LIMIT (see wagame/game/gacha.py).

ALTER TABLE players ADD COLUMN gems INTEGER NOT NULL DEFAULT 5000;
ALTER TABLE players ADD COLUMN last_daily_claim_date TEXT;

-- Backfill existing accounts (the DEFAULT only applies to NEW rows).
UPDATE players SET gems = 5000 WHERE gems = 0;

CREATE TABLE IF NOT EXISTS hero_shards (
    discord_user_id INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    pity_pulls      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discord_user_id, hero_id),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (hero_id)         REFERENCES heroes(id)               ON DELETE CASCADE,
    CHECK (count >= 0),
    CHECK (pity_pulls >= 0)
);

CREATE INDEX IF NOT EXISTS idx_hero_shards_player ON hero_shards(discord_user_id);
