-- Hero Diary — narrative DMs after gameplay events (PR #11).
--
-- Heroes "write" the player a 1-2 sentence in-character recap after a
-- kill, a gather, or a level-up. Per-(player, hero) throttle prevents
-- spam: at most one DM per hero per `DIARY_THROTTLE_SECONDS` (see
-- game/diary.py). `last_diary_at` is a unix timestamp, matching the
-- marches / hunt_marches convention.
--
-- `diary_dm_enabled` is a player-wide opt-out. Defaults to ON. The
-- toggle surfaces in the profile / hub so players can switch it without
-- knowing a slash command.

ALTER TABLE owned_heroes ADD COLUMN last_diary_at INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN diary_dm_enabled INTEGER NOT NULL DEFAULT 1;
