-- Hero Bonds follow-up — second hero slot on a hunt march.
--
-- `support_hero_id` is the wingman: primary still leads (full command +
-- speed buffs), support contributes 25% of its atk_eff and 50% of its
-- command/speed buffs, both heroes earn full kill XP, and the pair
-- accumulates bond points on every engagement (see game/bonds.py).
--
-- Nullable: solo marches keep working unchanged. ON DELETE SET NULL
-- mirrors `hero_id` so admin-deleted heroes don't break in-flight rows.

ALTER TABLE hunt_marches
    ADD COLUMN support_hero_id INTEGER REFERENCES heroes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_hunt_marches_support_hero
    ON hunt_marches(support_hero_id);
