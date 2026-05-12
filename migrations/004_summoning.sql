-- Summoning Gate: troop catalog, per-player counts, and the single
-- in-flight training job. Costs are food-only; tier gating starts at T1
-- and is widened by future research nodes via `players.unlocked_tier`.
--
-- We deliberately skip a "buildings" abstraction for now — the queue cap
-- and speed boost live as flat player columns and will be raised by the
-- research PR. Premium second slot is a backlog item.

ALTER TABLE players ADD COLUMN unlocked_tier INTEGER NOT NULL DEFAULT 1;
ALTER TABLE players ADD COLUMN training_queue_cap INTEGER NOT NULL DEFAULT 50;
ALTER TABLE players ADD COLUMN training_speed_boost_pct INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS troops (
    codename       TEXT    PRIMARY KEY,
    name           TEXT    NOT NULL,
    tier           INTEGER NOT NULL,
    role           TEXT,                     -- 'monster' | 'defender' | 'player' | NULL
    march_speed    INTEGER NOT NULL,
    carry_cap      REAL    NOT NULL,
    attack         INTEGER NOT NULL,
    hp             INTEGER NOT NULL,
    role_bonus     INTEGER NOT NULL DEFAULT 0,
    food_per_unit  INTEGER NOT NULL,
    train_seconds  INTEGER NOT NULL,
    CHECK (tier >= 1),
    CHECK (march_speed >= 0),
    CHECK (carry_cap >= 0),
    CHECK (attack >= 0),
    CHECK (hp >= 0),
    CHECK (food_per_unit >= 0),
    CHECK (train_seconds >= 0)
);

CREATE INDEX IF NOT EXISTS idx_troops_tier ON troops(tier);

-- One row per (player, troop) — count is the army inventory.
CREATE TABLE IF NOT EXISTS owned_troops (
    discord_user_id INTEGER NOT NULL,
    troop_codename  TEXT    NOT NULL,
    count           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discord_user_id, troop_codename),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (troop_codename)  REFERENCES troops(codename)         ON DELETE CASCADE,
    CHECK (count >= 0)
);

-- At most one active training job per player (PK enforces this).
-- When `finishes_at` passes, any DB-touching command lazily moves the
-- counts into `owned_troops` and deletes the row.
CREATE TABLE IF NOT EXISTS training_jobs (
    discord_user_id INTEGER PRIMARY KEY,
    troop_codename  TEXT    NOT NULL,
    count           INTEGER NOT NULL,
    started_at      INTEGER NOT NULL,
    finishes_at     INTEGER NOT NULL,
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (troop_codename)  REFERENCES troops(codename)         ON DELETE CASCADE,
    CHECK (count > 0),
    CHECK (finishes_at >= started_at)
);
