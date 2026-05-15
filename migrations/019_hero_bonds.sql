-- Hero Bonds (PR #20) — pair-level affinity bond between two heroes.
--
-- The (hero_a, hero_b) tuple is canonicalised so that hero_a_id <
-- hero_b_id; that gives us a single PK regardless of which hero is the
-- "primary" in the march. Points accumulate when both heroes ride
-- together; level is derived in code from `points` (see game/bonds.py).
--
-- last_bonded_at is the unix timestamp of the most recent contribution;
-- useful later for decay or "last seen together" UI.

CREATE TABLE IF NOT EXISTS hero_bonds (
    discord_user_id INTEGER NOT NULL,
    hero_a_id       INTEGER NOT NULL,
    hero_b_id       INTEGER NOT NULL,
    points          INTEGER NOT NULL DEFAULT 0,
    last_bonded_at  INTEGER NOT NULL,
    PRIMARY KEY (discord_user_id, hero_a_id, hero_b_id),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (hero_a_id)       REFERENCES heroes(id)               ON DELETE CASCADE,
    FOREIGN KEY (hero_b_id)       REFERENCES heroes(id)               ON DELETE CASCADE,
    CHECK (hero_a_id < hero_b_id),
    CHECK (points >= 0)
);

CREATE INDEX IF NOT EXISTS idx_hero_bonds_player ON hero_bonds(discord_user_id);
