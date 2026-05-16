-- Hero Bonds extension: persist the support hero on the hunt_marches
-- row so _engage_march can also grant XP to the support, not just the
-- primary. Nullable; legacy rows stay None.

ALTER TABLE hunt_marches
    ADD COLUMN support_hero_id INTEGER REFERENCES heroes(id) ON DELETE SET NULL;
