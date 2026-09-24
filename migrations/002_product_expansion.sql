-- Slovo product expansion marker.
-- Conditional column upgrades and lossless topic backfill are performed by
-- DB.init(), because SQLite cannot express ADD COLUMN IF NOT EXISTS safely.
INSERT OR IGNORE INTO schema_migrations(version) VALUES('002_product_expansion');
