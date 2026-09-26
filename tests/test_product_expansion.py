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
    assert "declared_source" not in result
    profile = client.get("/api/profile").json()["learning_profile"]
    assert profile["usage_role"] == "teacher" and "declared_source" not in profile
    assert database.user_profile(303)["declared_source"] == "friend"


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
    use(101, "Teacher")
    detail = client.get(f"/api/classes/{classroom['id']}").json()
    assert detail["url"].endswith(classroom["invite_code"])
    assert [member["telegram_id"] for member in detail["members"]] == [202]
    assert client.delete(f"/api/classes/{classroom['id']}/members/202").status_code == 200
    assert client.get(f"/api/classes/{classroom['id']}").json()["members"] == []


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


def test_16_regular_study_and_games_require_a_topic(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Scoped")
    database.add_cards(101, folder, [("one", "один"), ("two", "два")])
    assert client.post("/api/study", json={"folder_id": folder, "mode": "all"}).status_code == 422
    assert client.get(f"/api/study/unfinished?folder_id={folder}").status_code == 422
    assert client.get(f"/api/games/options?folder_id={folder}").status_code == 422
    assert client.post("/api/games", json={"folder_id": folder, "game_type": "match"}).status_code == 422


def test_17_topic_delete_moves_words_without_loss(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); folder = database.create_folder(101, "Move topic")
    source = database.create_topic(101, folder, "Source"); target = database.topics(101, folder)[0]["id"]
    database.add_cards(101, folder, [("one", "один")], source)
    response = client.post(f"/api/topics/{source}/delete", json={"target_topic_id": target})
    assert response.status_code == 200
    assert database.card_count(folder) == 1 and database.cards(folder)[0]["topic_id"] == target


def test_18_selected_assignment_and_teacher_metrics(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch); database.save_onboarding(101, 5, usage_role="teacher", complete=True)
    folder = database.create_folder(101, "Selected"); topic = database.topics(101, folder)[0]["id"]
    database.add_cards(101, folder, [("one", "один")], topic)
    classroom = client.post("/api/classes", json={"name": "Group", "language": "en"}).json()
    for user_id, name in ((202, "Student"), (303, "Friend")):
        use(user_id, name); client.post(f"/api/classes/join/{classroom['invite_code']}")
    use(101, "Teacher")
    assignment = client.post(f"/api/classes/{classroom['id']}/assignments", json={"title": "One student", "folder_id": folder, "topic_id": topic, "student_ids": [202]}).json()
    assert assignment["recipients"] == 1
    detail = client.get(f"/api/classes/{classroom['id']}").json()
    assert set(detail["progress"][0]) >= {"completion_percent", "known_words", "unknown_words", "accuracy", "learning_words", "repeat_words"}
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM assignment_recipients WHERE assignment_id=?", (assignment["id"],)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM analytics_events WHERE event_name='assignment_assigned'").fetchone()[0] == 1


def test_19_catalog_copy_assigns_every_word_to_a_topic(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch)
    response = client.post("/api/catalog/airport/copy")
    assert response.status_code == 201
    folder = response.json()["id"]
    cards = database.cards(folder)
    assert cards and all(card["topic_id"] for card in cards)
    assert database.topics(101, folder)[0]["name"] == "Без темы"


def test_20_new_cards_receive_cached_synonyms(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch);folder=database.create_folder(101,"Spanish","es","ru")
    topic=database.topics(101,folder)[0]["id"]
    monkeypatch.setenv("LANGUAGE_ENRICHMENT_ENABLED","1")
    monkeypatch.setattr(webapp,"synonym_lookup",lambda term,language:["alegre","contento"] if (term,language)==("feliz","es") else [])
    response=client.post(f"/api/folders/{folder}/cards",json={"topic_id":topic,"items":[{"term":"feliz","translation":"счастливый"}]})
    assert response.status_code==201
    card=client.get(f"/api/folders/{folder}").json()["cards"][0]
    assert card["synonyms"]==["alegre","contento"]


def test_21_onboarding_language_localizes_official_sets_and_study(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("LANGUAGE_ENRICHMENT_ENABLED","1")
    monkeypatch.setattr(webapp,"translate_texts",lambda values,source,target:[f"{target}:{value}" for value in values])
    response=client.patch("/api/onboarding",json={"step":5,"usage_role":"student","languages":["es"],"levels":["a1"],"complete":True})
    assert response.status_code==200
    detail=client.get("/api/catalog/airport").json()
    assert detail["source_lang"]=="es" and detail["cards"][0]["term"].startswith("es:")
    attached=client.post("/api/catalog/airport/attach").json();folder=attached["folder_id"]
    topic=database.topics(101,folder)[0]["id"]
    session=client.post("/api/study",json={"folder_id":folder,"topic_id":topic,"mode":"all"}).json()
    assert session["front_lang"]=="es" and session["front"].startswith("es:")


def teardown_module():
    webapp.app.dependency_overrides.clear()
