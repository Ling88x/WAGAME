-- Heroes (commanders) catalog and per-player ownership.
--
-- `heroes` is the static roster, synced from data/heroes.json on bot startup.
-- Use `codename` (game internal slug, e.g. "ghostpink") as the stable key.
--
-- `owned_heroes` is the per-player join table. UNIQUE on (player, hero):
-- duplicates from gacha increment `dupes_pending` (we'll define what to do
-- with them when the gacha PR lands — shards / star-up / dust).

CREATE TABLE IF NOT EXISTS heroes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    codename      TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    rarity        TEXT    NOT NULL,
    element       TEXT,
    house         TEXT,
    terrain       TEXT,
    release_date  TEXT,
    bonuses_json  TEXT    NOT NULL DEFAULT '[]',
    tags_json     TEXT    NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_heroes_rarity  ON heroes(rarity);
CREATE INDEX IF NOT EXISTS idx_heroes_house   ON heroes(house);
CREATE INDEX IF NOT EXISTS idx_heroes_terrain ON heroes(terrain);

CREATE TABLE IF NOT EXISTS owned_heroes (
    discord_user_id INTEGER NOT NULL,
    hero_id         INTEGER NOT NULL,
    level           INTEGER NOT NULL DEFAULT 1,
    dupes_pending   INTEGER NOT NULL DEFAULT 0,
    acquired_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (discord_user_id, hero_id),
    FOREIGN KEY (discord_user_id) REFERENCES players(discord_user_id) ON DELETE CASCADE,
    FOREIGN KEY (hero_id)         REFERENCES heroes(id)               ON DELETE CASCADE,
    CHECK (level >= 1),
    CHECK (dupes_pending >= 0)
);

CREATE INDEX IF NOT EXISTS idx_owned_heroes_player ON owned_heroes(discord_user_id);
