-- Bestiary (PR #13) — per-player encounter log keyed on (mob kind, level).
--
-- `mob_kind` is a forward-compat tag — only "tenebral" exists for now,
-- but future families (furies, void vesps) will reuse the same shape.
-- `slain_count` bumps on every kill, `encounter_count` on every attack
-- (chip or kill). Timestamps are unix.

CREATE TABLE IF NOT EXISTS bestiary_entries (
    discord_user_id  INTEGER NOT NULL,
    mob_kind         TEXT    NOT NULL,
    mob_level        INTEGER NOT NULL,
    slain_count      INTEGER NOT NULL DEFAULT 0,
    encounter_count  INTEGER NOT NULL DEFAULT 0,
    first_seen_at    INTEGER NOT NULL,
    last_seen_at     INTEGER NOT NULL,
    PRIMARY KEY (discord_user_id, mob_kind, mob_level),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (mob_level >= 1),
    CHECK (slain_count >= 0),
    CHECK (encounter_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_bestiary_player ON bestiary_entries(discord_user_id);
