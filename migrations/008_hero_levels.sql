-- Hero leveling — XP column on owned_heroes.
--
-- `level` already exists from migration 002 (default 1, CHECK >= 1).
-- This adds `xp` so we can track per-hero progress without scattering it
-- across other tables. Level cap is enforced in code
-- (wagame.game.hero_levels.STARTING_LEVEL_CAP); excess XP at cap stays
-- banked on this column so a future cap-raise (research) doesn't waste
-- player progress.

ALTER TABLE owned_heroes ADD COLUMN xp INTEGER NOT NULL DEFAULT 0;
