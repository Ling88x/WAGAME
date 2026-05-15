-- Player XP (PR #19) — account-level progression separate from per-hero
-- leveling. Same `xp_to_next` curve as heroes (100 * L^1.5). Each
-- player level pays a milestone gem reward + 1 random hero shard.
--
-- `players.player_level` already exists from migration 001 (default 1).
-- This adds the XP counter; level is derived by apply_player_xp.

ALTER TABLE players ADD COLUMN player_xp INTEGER NOT NULL DEFAULT 0;
