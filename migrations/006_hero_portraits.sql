-- Hero portraits: optional URL for the hero artwork shown as the embed
-- thumbnail. NULL when we don't have one yet (kohqs scrape hasn't filled it,
-- or the entry was hand-authored). The scraper writes this into heroes.json
-- and sync_heroes() upserts it.

ALTER TABLE heroes ADD COLUMN image_url TEXT;
