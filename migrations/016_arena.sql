-- Ghost Arena (PR #17) — async PvP. Background loop every 12h posts a
-- battle between two enrolled players in each configured server channel.
--
-- Matches are global (the pool of candidates is all enrolled players,
-- not just members of the posting server). Per-guild candidate pools
-- would require a player_guilds table; deferred.
--
-- ELO-style rating starts at 1000, K-factor 32. Win/loss/draw rolls
-- update arena_rating + the wins/losses/draws counters used in
-- /arena stats. Rewards: +100 gems winner, +25 loser, +50 each on draw.

ALTER TABLE players ADD COLUMN arena_rating   INTEGER NOT NULL DEFAULT 1000;
ALTER TABLE players ADD COLUMN arena_wins     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN arena_losses   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN arena_draws    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN arena_enrolled INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS arena_configs (
    guild_id       INTEGER PRIMARY KEY,
    channel_id     INTEGER NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    last_match_at  INTEGER NOT NULL DEFAULT 0,
    CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS arena_matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    a_user_id       INTEGER NOT NULL,
    b_user_id       INTEGER NOT NULL,
    winner_user_id  INTEGER,             -- NULL = draw
    rounds          INTEGER NOT NULL,
    log_json        TEXT    NOT NULL,
    fought_at       INTEGER NOT NULL,
    FOREIGN KEY (a_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (b_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_arena_matches_recent
    ON arena_matches(guild_id, fought_at DESC);
