-- Research: the source of truth for player progression bonuses.
--
-- Nodes are static and live in code (wagame/research_data.py); we only
-- track per-player progress here. `player_research.level` is the highest
-- level the player has completed for that node (0 = untouched).
--
-- `research_jobs` enforces "one in-flight research at a time" via the
-- PRIMARY KEY on `discord_user_id`. When a job finishes, the next DB-
-- touching command lazily applies the effect (bump the relevant player
-- column) and bumps player_research.level — auto-claim, same pattern as
-- training_jobs.
--
-- New player columns track bonuses that don't yet have a home:
--   gather_yield_pct       — applied at gather start, multiplies base roll
--   gather_speed_pct       — applied at gather start, shrinks march time
--   troop_attack_pct       — read by combat PR; stored now
--   troop_hp_pct           — read by combat PR; stored now
--
-- Existing columns also get scaled by research:
--   march_capacity, unlocked_tier, training_queue_cap,
--   training_speed_boost_pct — these all already exist.

ALTER TABLE players ADD COLUMN gather_yield_pct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN gather_speed_pct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN troop_attack_pct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN troop_hp_pct     INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS player_research (
    discord_user_id INTEGER NOT NULL,
    node_codename   TEXT    NOT NULL,
    level           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discord_user_id, node_codename),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (level >= 0)
);

CREATE TABLE IF NOT EXISTS research_jobs (
    discord_user_id INTEGER PRIMARY KEY,
    node_codename   TEXT    NOT NULL,
    target_level    INTEGER NOT NULL,
    started_at      INTEGER NOT NULL,
    finishes_at     INTEGER NOT NULL,
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (target_level >= 1),
    CHECK (finishes_at >= started_at)
);
