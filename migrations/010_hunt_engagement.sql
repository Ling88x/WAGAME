-- Hunt marches: track when the outbound leg lands ("engaged_at") so the
-- panel can render the two distinct phases.
--
-- Outbound phase: engaged_at IS NULL — march is en route, damage not
-- applied yet.
-- Return phase: engaged_at IS NOT NULL, resolved = 0 — hero is on the
-- way back; damage and rewards already credited at engagement.
-- Fully done: resolved = 1.

ALTER TABLE hunt_marches ADD COLUMN engaged_at INTEGER;
