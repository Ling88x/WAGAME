-- Gather marches now ride a commander hero — same shape as hunt marches.
--
-- `hero_id` is nullable because legacy in-flight marches predate this
-- column. New /gather panel forces selection, but old rows finish and
-- claim normally without one. On claim the row is deleted, so the
-- hero (when present) gets released for the next gather or hunt.

ALTER TABLE marches ADD COLUMN hero_id INTEGER REFERENCES heroes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_marches_hero ON marches(hero_id);
