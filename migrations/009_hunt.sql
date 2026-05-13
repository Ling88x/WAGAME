-- Tenebral Hunt — PR #7.
--
-- Energy is the throttle for hunting (cap 500, regen 1pt / 30s, lazy
-- regen formula keyed on `energy_updated_at`). Energy_updated_at is a
-- unix timestamp matching the marches/training_jobs convention.
--
-- `tenebral_spawns` keeps per-player mob HP between hits: a fresh spawn
-- gets full HP, attacks chip it down, kill removes the row.
--
-- `hunt_marches` snapshots an attack at click time (damage locked).
-- `resolved = 0` rows are in-flight faux-timers; on cog load we drain
-- any orphaned ones from a previous boot.
--
-- `hunt_daily_progress` tracks daily quota / reward claim. `reset_day`
-- is the YYYY-MM-DD label of the WA reset day (see game/daily.py).

ALTER TABLE players ADD COLUMN energy INTEGER NOT NULL DEFAULT 500;
ALTER TABLE players ADD COLUMN energy_updated_at INTEGER NOT NULL DEFAULT 0;

UPDATE players SET energy_updated_at = CAST(strftime('%s','now') AS INTEGER)
WHERE energy_updated_at = 0;

CREATE TABLE IF NOT EXISTS tenebral_spawns (
    discord_user_id INTEGER NOT NULL,
    level           INTEGER NOT NULL,
    hp_remaining    INTEGER NOT NULL,
    hp_max          INTEGER NOT NULL,
    spawned_at      INTEGER NOT NULL,
    PRIMARY KEY (discord_user_id, level),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (level >= 1 AND level <= 12),
    CHECK (hp_remaining >= 0),
    CHECK (hp_max > 0)
);

CREATE TABLE IF NOT EXISTS hunt_marches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id  INTEGER NOT NULL,
    level            INTEGER NOT NULL,
    hero_id          INTEGER,
    started_at       INTEGER NOT NULL,
    completes_at     INTEGER NOT NULL,
    damage           INTEGER NOT NULL,
    resolved         INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (hero_id) REFERENCES heroes(id) ON DELETE SET NULL,
    CHECK (level >= 1 AND level <= 12),
    CHECK (damage >= 0),
    CHECK (resolved IN (0, 1)),
    CHECK (completes_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_hunt_marches_player_pending
    ON hunt_marches(discord_user_id, resolved);

CREATE TABLE IF NOT EXISTS hunt_daily_progress (
    discord_user_id INTEGER NOT NULL,
    reset_day       TEXT    NOT NULL,
    kills           INTEGER NOT NULL DEFAULT 0,
    reward_claimed  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discord_user_id, reset_day),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (kills >= 0),
    CHECK (reward_claimed IN (0, 1))
);
