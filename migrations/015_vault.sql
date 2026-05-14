-- Witch's Vault (PR #15) — daily-open loot box with a streak multiplier.
--
-- One open per reset day (21:00 UTC boundary; see game/daily.py). Streak
-- counts consecutive days; skipping a reset day resets streak to 1 on
-- the next open. `last_vault_open_date` is the YYYY-MM-DD label of the
-- last opened reset day, matching `last_daily_claim_date` semantics.

ALTER TABLE players ADD COLUMN vault_streak INTEGER NOT NULL DEFAULT 0;
ALTER TABLE players ADD COLUMN last_vault_open_date TEXT;
