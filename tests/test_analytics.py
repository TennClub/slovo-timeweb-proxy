from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import analytics_bot
import webapp
from analytics import Analytics, format_report, normalize_ref
from slovo import DB


def make_db(tmp_path: Path) -> DB:
    return DB(str(tmp_path / "analytics.db"))


def set_created(database: DB, user_id: int, value: datetime):
    with database.conn() as con:
        con.execute("UPDATE users SET created_at=? WHERE telegram_id=?", (value.astimezone(timezone.utc).isoformat(), user_id))


def test_analytics_migration_indexes_and_idempotency(tmp_path):
    database = make_db(tmp_path); database.user(1, "Owner")
    analytics = Analytics(database, excluded=set())
    assert analytics.track(1, "app_open", session_id="session-123", idempotency_key="open:1")
    assert not analytics.track(1, "app_open", session_id="session-123", idempotency_key="open:1")
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM analytics_events").fetchone()[0] == 1
        assert con.execute("SELECT version FROM schema_migrations").fetchone()[0] == "001_product_analytics"
        indexes = {row[1] for row in con.execute("PRAGMA index_list(analytics_events)")}
        assert {"idx_analytics_events_user", "idx_analytics_events_name", "idx_analytics_events_created", "idx_analytics_events_session"} <= indexes


def test_first_touch_referral_and_validation(tmp_path):
    database = make_db(tmp_path)
    assert database.user(10, "New", ref_code="tutor_001", acquisition_source="tutor")
    assert not database.user(10, "New", ref_code="school_999", acquisition_source="school")
    with database.conn() as con:
        row = con.execute("SELECT acquisition_source,ref_code FROM users WHERE telegram_id=10").fetchone()
    assert tuple(row) == ("tutor", "tutor_001")
    assert normalize_ref("bad ref!") == "organic"
    assert normalize_ref("school_001") == "school_001"


def test_excluded_users_and_meaningful_activity(tmp_path):
    database = make_db(tmp_path); database.user(1, "Real"); database.user(99, "Internal")
    now = datetime.now(timezone.utc); analytics = Analytics(database, excluded={99})
    analytics.track(1, "app_open", created_at=now)
    analytics.track(1, "folder_created", {"folder_id": 1}, created_at=now)
    analytics.track(99, "folder_created", {"folder_id": 2}, created_at=now)
    values = analytics.period(now - timedelta(minutes=1), now + timedelta(minutes=1))
    assert values["total_users"] == 1 and values["active_users"] == 1
    assert values["events"]["folder_created"] == 1


def test_activation_uses_first_24_hours(tmp_path, monkeypatch):
    database = make_db(tmp_path); analytics = Analytics(database, excluded=set())
    registered = datetime(2026, 9, 1, 8, tzinfo=timezone.utc)
    database.user(1, "Fast"); database.user(2, "Late")
    set_created(database, 1, registered); set_created(database, 2, registered)
    analytics.track(1, "folder_created", {"folder_id": 1}, created_at=registered + timedelta(hours=1))
    analytics.track(2, "folder_created", {"folder_id": 2}, created_at=registered + timedelta(hours=25))
    result = analytics.activation(date(2026, 9, 1), as_of=registered + timedelta(days=3))
    assert result["registrations"] == 2
    assert result["stages"]["folder_created"] == 1


def test_retention_exact_days_and_weighted_cohorts(tmp_path):
    database = make_db(tmp_path); analytics = Analytics(database, excluded=set())
    # Moscow cohorts: Sep 1 has two users, Sep 2 has one user.
    for user_id, registered in [(1, datetime(2026, 9, 1, 9, tzinfo=timezone.utc)), (2, datetime(2026, 9, 1, 10, tzinfo=timezone.utc)), (3, datetime(2026, 9, 2, 9, tzinfo=timezone.utc))]:
        database.user(user_id, str(user_id)); set_created(database, user_id, registered)
    analytics.track(1, "test_started", {"test_session_id": "a", "folder_id": 1, "words_count": 1}, created_at=datetime(2026, 9, 2, 12, tzinfo=timezone.utc))
    analytics.track(3, "game_started", {"game_session_id": "b", "game_type": "match", "folder_id": 1}, created_at=datetime(2026, 9, 3, 12, tzinfo=timezone.utc))
    result = analytics.retention(date(2026, 9, 10))
    assert result["d1"]["size"] == 3 and result["d1"]["returned"] == 2
    assert result["d7"]["size"] == 3
    assert result["d30"]["percent"] is None


def test_api_tracks_bulk_words_and_single_test_completion(tmp_path, monkeypatch):
    database = make_db(tmp_path); database.user(1, "Owner")
    monkeypatch.setattr(webapp, "db", database)
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(id=1, first_name="Owner")
    client = TestClient(webapp.app)
    folder = client.post("/api/folders", json={"name": "Words", "source_lang": "en", "target_lang": "ru"}).json()["id"]
    items = [{"term": f"word-{i}", "translation": f"слово-{i}"} for i in range(3)]
    assert client.post(f"/api/folders/{folder}/cards", json={"items": items}).json()["added"] == 3
    topic = database.topics(1, folder)[0]["id"]
    session = client.post("/api/study", json={"folder_id": folder, "topic_id": topic, "mode": "all"}).json()
    while not session["done"]:
        session = client.post(f"/api/study/{session['id']}/answer", json={"success": True, "card_id": session["card_id"]}).json()
    # Retrying the final answer cannot create a second completion.
    assert client.post(f"/api/study/{session['id']}/answer", json={"success": True}).status_code == 409
    with database.conn() as con:
        word = con.execute("SELECT metadata FROM analytics_events WHERE event_name='word_added'").fetchone()[0]
        assert '"words_count": 3' in word
        assert con.execute("SELECT COUNT(*) FROM analytics_events WHERE event_name='test_completed'").fetchone()[0] == 1
        completed = con.execute("SELECT metadata FROM analytics_events WHERE event_name='test_completed'").fetchone()[0]
        assert '"known_count": 3' in completed and '"unknown_count": 0' in completed
    webapp.app.dependency_overrides.clear()


def test_http_retry_does_not_duplicate_folder_words_or_events(tmp_path, monkeypatch):
    database = make_db(tmp_path); database.user(1, "Owner")
    monkeypatch.setattr(webapp, "db", database)
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(id=1, first_name="Owner")
    client = TestClient(webapp.app)
    folder_payload={"name":"Retry safe","source_lang":"en","target_lang":"ru","request_id":"folder-request-001"}
    first=client.post("/api/folders",json=folder_payload);second=client.post("/api/folders",json=folder_payload)
    assert first.json()["id"] == second.json()["id"]
    folder=first.json()["id"];cards_payload={"items":[{"term":"one","translation":"один"},{"term":"two","translation":"два"}],"request_id":"cards-request-001"}
    assert client.post(f"/api/folders/{folder}/cards",json=cards_payload).json()["added"] == 2
    assert client.post(f"/api/folders/{folder}/cards",json=cards_payload).json()["added"] == 2
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM folders WHERE owner_id=1").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM cards WHERE folder_id=?",(folder,)).fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM analytics_events WHERE event_name='folder_created'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM analytics_events WHERE event_name='word_added'").fetchone()[0] == 1
    webapp.app.dependency_overrides.clear()


def test_empty_report_has_no_division_error(tmp_path):
    analytics = Analytics(make_db(tmp_path), excluded=set())
    report = format_report(analytics.snapshot(date.today()))
    assert "DAU: 0" in report and "D1: —" in report


def test_analytics_bot_owner_authorization(monkeypatch):
    monkeypatch.setattr(analytics_bot, "settings", lambda: ("token", 42))
    assert analytics_bot.authorized(SimpleNamespace(from_user=SimpleNamespace(id=42)))
    assert not analytics_bot.authorized(SimpleNamespace(from_user=SimpleNamespace(id=7)))
