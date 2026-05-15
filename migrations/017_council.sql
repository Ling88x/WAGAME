-- Witch's Council (PR #18) — weekly server-wide collective quest.
--
-- ONE active quest at a time globally. All players auto-contribute via
-- their normal play; per-guild scope only governs WHERE the spawn /
-- progress / end announcements get posted (mirrors how Arena works).
--
-- council_quests row owns: kind, target, current progress, lifespan,
-- and a settled flag so the resolver doesn't double-pay rewards on a
-- restart-race.
--
-- council_contributions is the per-quest scoreboard. PRIMARY KEY on
-- (quest_id, user_id) makes upsert idempotent.

CREATE TABLE IF NOT EXISTS council_configs (
    guild_id   INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS council_quests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,
    target      INTEGER NOT NULL,
    progress    INTEGER NOT NULL DEFAULT 0,
    started_at  INTEGER NOT NULL,
    ends_at     INTEGER NOT NULL,
    settled     INTEGER NOT NULL DEFAULT 0,
    spawn_message_ids TEXT NOT NULL DEFAULT '{}',  -- json {guild_id: message_id}
    CHECK (target > 0),
    CHECK (settled IN (0, 1)),
    CHECK (ends_at >= started_at)
);

CREATE INDEX IF NOT EXISTS idx_council_quests_active
    ON council_quests(settled, ends_at);

CREATE TABLE IF NOT EXISTS council_contributions (
    quest_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    amount   INTEGER NOT NULL DEFAULT 0,
    last_contribution_at INTEGER NOT NULL,
    PRIMARY KEY (quest_id, user_id),
    FOREIGN KEY (quest_id) REFERENCES council_quests(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)  REFERENCES players(discord_user_id) ON DELETE CASCADE,
    CHECK (amount >= 0)
);

CREATE INDEX IF NOT EXISTS idx_council_contributions_quest
    ON council_contributions(quest_id, amount DESC);
