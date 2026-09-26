-- Card synonyms and cached official-set translations are added conditionally
-- by DB.init() so existing SQLite databases remain intact.
INSERT OR IGNORE INTO schema_migrations(version) VALUES('004_language_enrichment');
