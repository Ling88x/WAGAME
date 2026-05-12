-- Gathering: timed resource-collection actions consuming a march slot.
--
-- `marches` holds one row per in-flight or finished-but-unclaimed gather.
-- When the player claims it, the resources are added to `players` and the
-- row is deleted. There is no separate "claimed" state — pending vs done is
-- a function of clock time vs `finishes_at`.
--
-- Slot accounting: a player can have at most `players.march_capacity` rows
-- in `marches` at once. Slots have no fixed identity; the count is what
-- matters. Capacity starts at 2 and is raised via research (cap 6, enforced
-- in code).
--
-- `yield_amount` and `crit` are rolled at start time and frozen on the row,
-- so the result is deterministic from the moment the gather begins.

UPDATE players SET march_capacity = 2 WHERE march_capacity < 2;

CREATE TABLE IF NOT EXISTS marches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_user_id INTEGER NOT NULL,
    resource        TEXT    NOT NULL,
    started_at      INTEGER NOT NULL,
    finishes_at     INTEGER NOT NULL,
    yield_amount    INTEGER NOT NULL,
    crit            INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (resource IN ('gold', 'food', 'wood')),
    CHECK (yield_amount >= 0),
    CHECK (crit IN (0, 1)),
    CHECK (finishes_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_marches_player ON marches(discord_user_id);
