-- One-time rollout marker. DB.init() resets onboarding for users that already
-- existed before this release, then records this marker so it never repeats.
INSERT OR IGNORE INTO schema_migrations(version) VALUES('003_learning_profile');
