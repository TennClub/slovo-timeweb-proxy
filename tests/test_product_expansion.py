import sqlite3

from fastapi.testclient import TestClient

import webapp
from slovo import DB, MAX_CARDS_PER_TOPIC


def setup(tmp_path, monkeypatch):
    database = DB(str(tmp_path / "expansion.db"))
    for user_id, name in ((101, "Teacher"), (202, "Student"), (303, "Friend")):
        database.user(user_id, name)
    monkeypatch.setattr(webapp, "db", database)
    monkeypatch.setenv("BOT_USERNAME", "LangSlovo_Bot")
    monkeypatch.setattr(webapp, "check_channel", lambda user_id: {"configured": True, "subscribed": True, "url": "https://t.me/slovo"})
    use(101, "Teacher")
    return database, TestClient(webapp.app)


def use(user_id, name):
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(id=user_id, first_name=name)


def test_01_new_folder_has_system_topic(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch)
    folder = client.post("/api/folders", json={"name": "IELTS", "source_lang": "en", "target_lang": "ru"}).json()
    assert [(x["name"], x["is_system"]) for x in database.topics(101, folder["id"])] == [("Без темы", 1)]


def test_02_bulk_words_split_into_topics_without_loss(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Bulk")
    items = [{"term": f"w{i}", "translation": f"t{i}"} for i in range(65)]
    assert client.post(f"/api/folders/{folder}/cards", json={"items": items}).status_code == 201
    assert [x["word_count"] for x in database.topics(101, folder)] == [30, 30, 5]


def test_03_explicit_topic_has_strict_limit(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Limit")
    topic = database.topics(101, folder)[0]["id"]
    items = [{"term": f"w{i}", "translation": f"t{i}"} for i in range(MAX_CARDS_PER_TOPIC + 1)]
    response = client.post(f"/api/folders/{folder}/cards", json={"topic_id": topic, "items": items})
    assert response.status_code == 409 and database.card_count(folder) == 0


def test_04_create_and_rename_topic(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Topics")
    topic = client.post(f"/api/folders/{folder}/topics", json={"name": "Travel"}).json()
    assert client.patch(f"/api/topics/{topic['id']}", json={"name": "Airport"}).json()["name"] == "Airport"


def test_05_move_word_between_topics(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Move")
    first = database.topics(101, folder)[0]["id"]; second = database.create_topic(101, folder, "Second")
    card = database.add_cards(101, folder, [("one", "один")], first)[0]
    assert client.patch(f"/api/cards/{card}/topic", json={"topic_id": second}).json()["topic_id"] == second


def test_06_study_is_scoped_to_topic(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Study")
    one = database.topics(101, folder)[0]["id"]; two = database.create_topic(101, folder, "Two")
    database.add_cards(101, folder, [("one", "один")], one); database.add_cards(101, folder, [("two", "два")], two)
    session = client.post("/api/study", json={"folder_id": folder, "topic_id": two, "mode": "all"}).json()
    assert session["total"] == 1 and session["front"] == "two"


def test_07_invite_requires_explicit_acceptance(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Shared")
    token = client.post(f"/api/folders/{folder}/invites", json={"role": "member"}).json()["token"]
    use(202, "Student"); preview = client.get(f"/api/invites/{token}").json()
    assert preview["folder_name"] == "Shared" and database.role(202, folder) is None
    assert client.post(f"/api/invites/{token}/join").status_code == 200 and database.role(202, folder) == "member"


def test_08_declined_invite_does_not_grant_access(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Shared")
    token = database.create_invite(101, folder, "member"); use(202, "Student")
    assert client.post(f"/api/invites/{token}/decline").status_code == 200 and database.role(202, folder) is None


def test_09_revoked_invite_is_rejected(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Shared")
    token = database.create_invite(101, folder, "member"); database.revoke_invite(folder, token); use(202, "Student")
    assert client.post(f"/api/invites/{token}/join").status_code == 404


def test_10_referral_is_first_touch_and_no_self_referral(tmp_path, monkeypatch):
    database, _ = setup(tmp_path, monkeypatch); owner = database.user_profile(101)["personal_ref_code"]
    assert database.apply_referral(202, owner) and not database.apply_referral(202, database.user_profile(303)["personal_ref_code"])
    assert not database.apply_referral(101, owner) and database.referral_count(101) == 1


def test_11_onboarding_persists_and_completes(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); use(303, "Friend")
    with database.conn() as con: con.execute("UPDATE users SET onboarding_completed_at=NULL,onboarding_step=0 WHERE telegram_id=303")
    assert client.patch("/api/onboarding", json={"step": 1, "usage_role": "teacher"}).json()["step"] == 1
    result = client.patch("/api/onboarding", json={"step": 5, "declared_source": "friend", "complete": True}).json()
    assert not result["required"] and result["usage_role"] == "teacher"


def test_12_official_set_is_locked_server_side(tmp_path, monkeypatch):
    _, client = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "check_channel", lambda user_id: {"configured": True, "subscribed": False, "url": "x"})
    assert client.post("/api/catalog/airport/attach").status_code == 403


def test_13_teacher_can_create_class_and_student_can_join(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch)
    database.save_onboarding(101, 5, usage_role="teacher", complete=True)
    classroom = client.post("/api/classes", json={"name": "Group A", "language": "en"}).json()
    use(202, "Student"); joined = client.post(f"/api/classes/join/{classroom['invite_code']}")
    assert joined.status_code == 200


def test_14_non_teacher_cannot_create_class(tmp_path, monkeypatch):
    _, client = setup(tmp_path, monkeypatch); use(202, "Student")
    assert client.post("/api/classes", json={"name": "Nope"}).status_code == 403


def test_15_assignment_reuses_content_and_tracks_completion(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); database.save_onboarding(101, 5, usage_role="teacher", complete=True)
    folder = database.create_folder(101, "Homework"); database.add_cards(101, folder, [("one", "один")])
    classroom = client.post("/api/classes", json={"name": "Group", "language": "en"}).json(); use(202, "Student")
    client.post(f"/api/classes/join/{classroom['invite_code']}"); use(101, "Teacher")
    assignment = client.post(f"/api/classes/{classroom['id']}/assignments", json={"title": "Lesson 1", "folder_id": folder}).json()
    use(202, "Student"); session = client.post("/api/study", json={"folder_id": folder, "assignment_id": assignment["id"], "mode": "all"}).json()
    session = client.post(f"/api/study/{session['id']}/answer", json={"success": True, "card_id": session["card_id"]}).json()
    with database.conn() as con: status = con.execute("SELECT status FROM assignment_recipients WHERE assignment_id=? AND user_id=202", (assignment["id"],)).fetchone()[0]
    assert session["done"] and status == "completed" and database.card_count(folder) == 1


def teardown_module():
    webapp.app.dependency_overrides.clear()
