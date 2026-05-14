-- Tenebral Sightings (PR #12) — random rare mob spawns delivered to
-- active players via DM. One active per player; engagement window is
-- short (EXPIRY_SECONDS in game/sightings.py); rewards scale ~3× over
-- a regular hunt of the same level plus random hero shards.
--
-- `players.next_sighting_at` is the earliest unix time a player is
-- eligible for the next sighting. Backfilled with a 1-hour grace so
-- the very first loop tick after migration doesn't unleash a wave.

CREATE TABLE IF NOT EXISTS tenebral_sightings (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id      INTEGER NOT NULL,
    level                INTEGER NOT NULL,
    hp_remaining         INTEGER NOT NULL,
    hp_max               INTEGER NOT NULL,
    spawned_at           INTEGER NOT NULL,
    expires_at           INTEGER NOT NULL,
    bonus_rss            INTEGER NOT NULL,
    bonus_xp             INTEGER NOT NULL DEFAULT 0,
    bonus_shard_hero_id  INTEGER,
    bonus_shard_count    INTEGER NOT NULL DEFAULT 0,
    claimed              INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (discord_user_id)     REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (bonus_shard_hero_id) REFERENCES heroes(id)               ON DELETE SET NULL,
    CHECK (level >= 1 AND level <= 12),
    CHECK (hp_remaining >= 0),
    CHECK (hp_max > 0),
    CHECK (bonus_rss >= 0),
    CHECK (bonus_xp >= 0),
    CHECK (bonus_shard_count >= 0),
    CHECK (claimed IN (0, 1)),
    CHECK (expires_at >= spawned_at)
);

CREATE INDEX IF NOT EXISTS idx_sightings_active
    ON tenebral_sightings(discord_user_id, claimed);

CREATE INDEX IF NOT EXISTS idx_sightings_expires
    ON tenebral_sightings(expires_at);

ALTER TABLE players ADD COLUMN next_sighting_at INTEGER NOT NULL DEFAULT 0;

-- Existing players: 1-hour grace before first eligibility (so the first
-- loop tick after migration doesn't fire DMs to everyone at once).
UPDATE players
   SET next_sighting_at = CAST(strftime('%s','now') AS INTEGER) + 3600
 WHERE next_sighting_at = 0;
