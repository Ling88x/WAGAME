-- Initial schema: players table and bookkeeping.
-- Per-user-globally model: PRIMARY KEY is the Discord user id.

CREATE TABLE IF NOT EXISTS players (
    discord_user_id INTEGER PRIMARY KEY,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen       TEXT    NOT NULL DEFAULT (datetime('now')),
    player_level    INTEGER NOT NULL DEFAULT 1,
    march_capacity  INTEGER NOT NULL DEFAULT 1,
    gold            INTEGER NOT NULL DEFAULT 0,
    food            INTEGER NOT NULL DEFAULT 0,
    wood            INTEGER NOT NULL DEFAULT 0,
    CHECK (gold >= 0),
    CHECK (food >= 0),
    CHECK (wood >= 0),
    CHECK (player_level >= 1),
    CHECK (march_capacity >= 1)
);
