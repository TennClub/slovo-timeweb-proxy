import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

import webapp
from slovo import DB


def make_db(tmp_path: Path):
    database = DB(str(tmp_path / "web.db"))
    database.user(101, "Teacher")
    database.user(202, "Student")
    return database


def use_user(user_id, name):
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(id=user_id, first_name=name)


def test_telegram_init_data_validation():
    token = "123456:test-token"
    values = {
        "auth_date": str(int(time.time())),
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": 101, "first_name": "Teacher"}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    assert webapp.validate_init_data(urlencode(values), token).id == 101


def test_folder_cards_and_study_flow(tmp_path, monkeypatch):
    database = make_db(tmp_path)
    monkeypatch.setattr(webapp, "db", database)
    use_user(101, "Teacher")
    client = TestClient(webapp.app)

    created = client.post("/api/folders", json={"name":"Spanish", "source_lang":"es", "target_lang":"ru"})
    assert created.status_code == 201
    folder_id = created.json()["id"]
    cards = client.post(f"/api/folders/{folder_id}/cards", json={"items":[
        {"term":"hola", "translation":"привет"},
        {"term":"libro", "translation":"книга"},
    ]})
    assert cards.json() == {"added":2, "duplicates":0}

    session = client.post("/api/study", json={"folder_id":folder_id, "mode":"due", "direction":"rev"}).json()
    assert session["front"] in {"привет", "книга"}
    assert session["back"] in {"hola", "libro"}
    while not session["done"]:
        session = client.post(f"/api/study/{session['id']}/answer", json={"success":True}).json()
    assert session["total"] == 2 and session["correct"] == 2


def test_member_cannot_edit_shared_folder(tmp_path, monkeypatch):
    database = make_db(tmp_path)
    monkeypatch.setattr(webapp, "db", database)
    folder_id = database.create_folder(101, "Shared")
    token = database.create_invite(101, folder_id, "member")
    assert database.join(202, token) == folder_id
    use_user(202, "Student")
    client = TestClient(webapp.app)
    response = client.post(f"/api/folders/{folder_id}/cards", json={"items":[{"term":"a","translation":"b"}]})
    assert response.status_code == 403


def test_invite_link_opens_and_joins_mini_app(tmp_path, monkeypatch):
    database = make_db(tmp_path)
    monkeypatch.setattr(webapp, "db", database)
    monkeypatch.setenv("BOT_USERNAME", "LangSlovo_Bot")
    use_user(101, "Teacher")
    client = TestClient(webapp.app)
    folder_id = database.create_folder(101, "Shared")

    created = client.post(f"/api/folders/{folder_id}/invites", json={"role":"member"})
    assert created.status_code == 200
    invite_url = created.json()["url"]
    assert invite_url.startswith("https://t.me/LangSlovo_Bot?start=folder_")

    token = invite_url.split("start=", 1)[1]
    use_user(202, "Student")
    joined = client.post(f"/api/invites/{token}/join")
    assert joined.status_code == 200
    assert joined.json()["folder_id"] == folder_id
    assert database.role(202, folder_id) == "member"


def teardown_module():
    webapp.app.dependency_overrides.clear()
