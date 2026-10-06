-- Topic naming and the lossless navigation upgrade are applied conditionally
-- by DB.init() so existing folders, cards and progress remain intact.
INSERT OR IGNORE INTO schema_migrations(version) VALUES('005_topic_navigation');
