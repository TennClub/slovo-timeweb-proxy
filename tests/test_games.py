from pathlib import Path

from fastapi.testclient import TestClient

import webapp
from slovo import DB


def setup_game(tmp_path: Path, monkeypatch, count=10):
    database = DB(str(tmp_path / "games.db"))
    database.user(1, "Player")
    folder = database.create_folder(1, "Game words", "en", "ru")
    words = [
        ("airport", "аэропорт"), ("grateful", "благодарный"), ("neighbour", "сосед"),
        ("letter", "письмо"), ("garden", "сад"), ("window", "окно"),
        ("pencil", "карандаш"), ("school", "школа"), ("market", "рынок"),
        ("friend", "друг"),
    ][:count]
    database.add_cards(1, folder, words)
    monkeypatch.setattr(webapp, "db", database)
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(id=1, first_name="Player")
    return database, folder, TestClient(webapp.app)


def action(client, round_id, event_id, **payload):
    return client.post(f"/api/games/{round_id}/action", json={"event_id": event_id, **payload})


def topic_id(database, folder):
    return database.topics(1, folder)[0]["id"]


def test_game_options_report_actual_short_rounds(tmp_path, monkeypatch):
    database, folder, client = setup_game(tmp_path, monkeypatch, count=3)
    games = client.get(f"/api/games/options?folder_id={folder}&topic_id={topic_id(database, folder)}").json()["games"]
    assert games["match"] == {"size": 3, "available": True, "reason": None}
    assert games["listen"]["available"] is False
    assert games["listen"]["reason"] == "listen_needs_four"
    assert games["build"]["size"] == 3


def test_match_round_is_finite_and_duplicate_action_is_idempotent(tmp_path, monkeypatch):
    database, folder, client = setup_game(tmp_path, monkeypatch)
    game = client.post("/api/games", json={"folder_id": folder, "topic_id": topic_id(database, folder), "game_type": "match"}).json()
    assert game["total"] == 6
    wrong_left = game["terms"][0]["card_id"]
    wrong_right = next(x["card_id"] for x in game["translations"] if x["card_id"] != wrong_left)
    first = action(client, game["id"], "wrong-event", action="match", card_id=wrong_left, translation_card_id=wrong_right)
    duplicate = action(client, game["id"], "wrong-event", action="match", card_id=wrong_left, translation_card_id=wrong_right)
    assert first.status_code == duplicate.status_code == 200
    assert duplicate.json()["result"]["wrong_connections"] == 1

    current = duplicate.json()
    for term in current["terms"]:
        if term["card_id"] in current["matched_ids"]:
            continue
        current = action(client, game["id"], f"match-{term['card_id']}", action="match",
                         card_id=term["card_id"], translation_card_id=term["card_id"]).json()
    assert current["done"] and current["result"]["matched"] == 6
    assert current["result"]["wrong_connections"] == 1
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM game_events WHERE round_id=?", (game["id"],)).fetchone()[0] == 7
        assert con.execute("SELECT COUNT(*) FROM progress WHERE user_id=1",).fetchone()[0] == 0


def test_listen_categories_sum_and_technical_skip_is_not_an_error(tmp_path, monkeypatch):
    database, folder, client = setup_game(tmp_path, monkeypatch, count=4)
    game = client.post("/api/games", json={"folder_id": folder, "topic_id": topic_id(database, folder), "game_type": "listen"}).json()
    q = game["question"]
    assert q["speech_text"]
    assert "term" not in q
    wrong = next(x["card_id"] for x in q["choices"] if x["card_id"] != q["card_id"])
    game = action(client, game["id"], "listen-1", action="answer", card_id=q["card_id"], choice_id=wrong).json()
    q = game["question"]
    game = action(client, game["id"], "listen-2", action="unknown", card_id=q["card_id"]).json()
    q = game["question"]
    game = action(client, game["id"], "listen-3", action="skip", card_id=q["card_id"]).json()
    q = game["question"]
    game = action(client, game["id"], "listen-4", action="answer", card_id=q["card_id"], choice_id=q["card_id"]).json()
    result = game["result"]
    assert game["done"]
    assert result["correct"] + result["wrong"] + result["unknown"] + result["skipped"] == 4
    assert result["skipped"] == 1 and result["accuracy"] == 33
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM progress").fetchone()[0] == 0


def test_build_hint_wrong_answer_and_repeated_letters(tmp_path, monkeypatch):
    database, folder, client = setup_game(tmp_path, monkeypatch, count=4)
    cards = client.get(f"/api/folders/{folder}?limit=50").json()["cards"]
    letter_id = next(card["id"] for card in cards if card["term"] == "letter")
    game = client.post("/api/games", json={"folder_id": folder, "topic_id": topic_id(database, folder), "game_type": "build", "card_ids": [letter_id]}).json()
    q = game["question"]
    wrong = action(client, game["id"], "build-wrong", action="check", card_id=q["card_id"], assembled="lettre").json()
    assert not wrong["done"] and wrong["outcome"] == "wrong"
    hinted = action(client, game["id"], "build-hint", action="hint", card_id=q["card_id"]).json()
    assert not hinted["done"] and hinted["outcome"] == "hint"
    done = action(client, game["id"], "build-right", action="check", card_id=q["card_id"], assembled="letter").json()
    assert done["done"] and done["result"] == {"total": 1, "first": 0, "helped": 1, "unknown": 0, "mistakes": 1}


def test_pause_resume_and_access_revocation(tmp_path, monkeypatch):
    database, folder, client = setup_game(tmp_path, monkeypatch, count=4)
    topic = topic_id(database, folder)
    game = client.post("/api/games", json={"folder_id": folder, "topic_id": topic, "game_type": "listen"}).json()
    assert client.post(f"/api/games/{game['id']}/pause").json()["ok"]
    assert client.get(f"/api/games/unfinished?folder_id={folder}&topic_id={topic}").json()["status"] == "paused"
    assert client.get(f"/api/games/{game['id']}").json()["status"] == "in_progress"
    with database.conn() as con:
        con.execute("DELETE FROM memberships WHERE folder_id=? AND user_id=?", (folder, 1))
    assert client.get(f"/api/games/{game['id']}").status_code == 404


def teardown_module():
    webapp.app.dependency_overrides.clear()
