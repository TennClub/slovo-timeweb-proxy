CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
  event_name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  session_id TEXT,
  idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS analytics_report_deliveries (
  report_date TEXT PRIMARY KEY,
  delivered_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'sent'
);

CREATE TABLE IF NOT EXISTS request_deduplication (
  user_id INTEGER NOT NULL,
  operation TEXT NOT NULL,
  request_id TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(user_id, operation, request_id)
);

CREATE INDEX IF NOT EXISTS idx_request_dedup_created ON request_deduplication(created_at);

CREATE INDEX IF NOT EXISTS idx_analytics_events_user ON analytics_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_events_name ON analytics_events(event_name, created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_events_created ON analytics_events(created_at);
CREATE INDEX IF NOT EXISTS idx_analytics_events_session ON analytics_events(session_id);
CREATE INDEX IF NOT EXISTS idx_users_acquisition_source ON users(acquisition_source);
CREATE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code);
