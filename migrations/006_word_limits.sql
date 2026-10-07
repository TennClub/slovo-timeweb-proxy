-- Keep the product limit enforceable in SQLite as well as in the API.
-- Existing data is preserved; the new limits apply to future inserts.
DROP TRIGGER IF EXISTS limit_cards_per_topic;
CREATE TRIGGER limit_cards_per_topic
BEFORE INSERT ON cards
WHEN NEW.topic_id IS NOT NULL AND (SELECT COUNT(*) FROM cards WHERE topic_id=NEW.topic_id) >= 50
BEGIN
 SELECT RAISE(ABORT,'topic_word_limit');
END;

DROP TRIGGER IF EXISTS limit_cards_per_folder;
CREATE TRIGGER limit_cards_per_folder
BEFORE INSERT ON cards
WHEN (SELECT COUNT(*) FROM cards WHERE folder_id=NEW.folder_id) >= 50
BEGIN
 SELECT RAISE(ABORT,'folder_word_limit');
END;

INSERT OR IGNORE INTO schema_migrations(version) VALUES('006_word_limits');
