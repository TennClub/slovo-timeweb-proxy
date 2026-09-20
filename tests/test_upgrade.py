import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import webapp
from slovo import DB


def setup_app(tmp_path: Path, monkeypatch, user_id=1):
    database = DB(str(tmp_path / "upgrade.db"))
    database.user(1, "Owner")
    database.user(2, "Student")
    monkeypatch.setattr(webapp, "db", database)
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(id=user_id, first_name="Owner" if user_id == 1 else "Student")
    return database, TestClient(webapp.app)


def test_migration_preserves_existing_cards(tmp_path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE users (telegram_id INTEGER PRIMARY KEY, name TEXT, last_folder_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE folders (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, owner_id INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE memberships (folder_id INTEGER,user_id INTEGER,role TEXT,PRIMARY KEY(folder_id,user_id));
    CREATE TABLE invitations (token TEXT PRIMARY KEY,folder_id INTEGER,role TEXT,created_by INTEGER,revoked INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT,folder_id INTEGER,term TEXT NOT NULL,translation TEXT NOT NULL,created_by INTEGER NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE progress (user_id INTEGER,card_id INTEGER,level INTEGER DEFAULT 0,due_date TEXT,last_success TEXT,PRIMARY KEY(user_id,card_id));
    CREATE TABLE drafts (user_id INTEGER PRIMARY KEY,kind TEXT,payload TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE sessions (id TEXT PRIMARY KEY,user_id INTEGER,folder_id INTEGER,mode TEXT,queue TEXT,pos INTEGER DEFAULT 0,current_card INTEGER,phase TEXT DEFAULT 'ask',answered INTEGER DEFAULT 0,errors TEXT DEFAULT '',extra_counts TEXT DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    INSERT INTO users(telegram_id,name) VALUES(1,'Owner');
    INSERT INTO folders(name,owner_id) VALUES('Legacy',1);
    INSERT INTO memberships VALUES(1,1,'owner');
    INSERT INTO cards(folder_id,term,translation,created_by) VALUES(1,'apple','яблоко',1);
    """)
    con.commit(); con.close()
    database = DB(str(path))
    assert database.card(1, 1)["term"] == "apple"
    with database.conn() as migrated:
        columns = {row[1] for row in migrated.execute("PRAGMA table_info(cards)")}
        assert {"transcription", "example", "example_translation", "audio_url"} <= columns
        assert migrated.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_fast_home_does_not_use_per_folder_count_queries(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    for i in range(25):
        folder = database.create_folder(1, f"Folder {i}")
        database.add_cards(1, folder, [("word", "слово")])
    monkeypatch.setattr(database, "card_count", lambda *_: (_ for _ in ()).throw(AssertionError("N+1 card count")))
    monkeypatch.setattr(database, "due_count", lambda *_: (_ for _ in ()).throw(AssertionError("N+1 due count")))
    response = client.get("/api/home")
    assert response.status_code == 200
    assert len(response.json()["folders"]) == 25
    assert response.json()["due_count"] == 25


def test_long_name_metadata_search_and_pagination(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    name = "Cambridge Vocabulary for IELTS; Page 169, Word List"
    folder = client.post("/api/folders", json={"name": name, "source_lang": "en", "target_lang": "ru"}).json()
    items = [{"term": f"word {i}", "translation": f"перевод {i}"} for i in range(50)]
    assert client.post(f"/api/folders/{folder['id']}/cards", json={"items": items}).status_code == 201
    page = client.get(f"/api/folders/{folder['id']}?limit=20").json()
    assert page["name"] == name and len(page["cards"]) == 20 and page["has_more"]
    found = client.get(f"/api/folders/{folder['id']}?q=word%2049").json()
    assert [card["term"] for card in found["cards"]] == ["word 49"]


def test_folder_accepts_at_most_50_words_atomically(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Limit")
    first_49 = [{"term": f"word {i}", "translation": f"перевод {i}"} for i in range(49)]
    assert client.post(f"/api/folders/{folder}/cards", json={"items": first_49}).status_code == 201

    too_many = client.post(
        f"/api/folders/{folder}/cards",
        json={"items": [
            {"term": "word 49", "translation": "перевод 49"},
            {"term": "word 50", "translation": "перевод 50"},
        ]},
    )
    assert too_many.status_code == 409
    assert too_many.json()["detail"] == "folder_word_limit"
    assert database.card_count(folder) == 49

    assert client.post(
        f"/api/folders/{folder}/cards",
        json={"items": [{"term": "word 49", "translation": "перевод 49"}]},
    ).status_code == 201
    assert database.card_count(folder) == 50

    blocked = client.post(
        f"/api/folders/{folder}/cards",
        json={"items": [{"term": "overflow", "translation": "лишнее"}]},
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "folder_word_limit"
    assert database.card_count(folder) == 50


def test_card_transcription_and_cached_audio(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "English", "en", "ru")
    database.add_cards(1, folder, [("apprehensive", "обеспокоенный")])
    card_id = database.cards(folder)[0]["id"]
    payload = {"term": "apprehensive", "translation": "обеспокоенный", "transcription": "/ˌæprɪˈhensɪv/"}
    updated = client.patch(f"/api/cards/{card_id}", json=payload)
    assert updated.status_code == 200 and updated.json()["card"]["transcription"] == payload["transcription"]
    assert "example" not in updated.json()["card"] and "example_translation" not in updated.json()["card"]
    database.update_card(card_id, "audio_url", "https://example.org/apprehensive.mp3")
    audio = client.post(f"/api/cards/{card_id}/pronunciation").json()
    assert audio["available"] and audio["audio_url"].endswith(".mp3")


def test_pronunciation_unavailable_for_non_english_without_audio(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Spanish", "es", "ru")
    database.add_cards(1, folder, [("hola", "привет")])
    card_id = database.cards(folder)[0]["id"]
    result = client.post(f"/api/cards/{card_id}/pronunciation").json()
    assert result == {"available": False, "transcription": None, "audio_url": None}


def test_existing_transcription_does_not_block_missing_audio_lookup(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "English", "en", "ru")
    database.add_cards(1, folder, [("characteristic", "особенность")])
    card_id = database.cards(folder)[0]["id"]
    database.update_card(card_id, "transcription", "/manual/")
    monkeypatch.setattr(webapp, "dictionary_lookup", lambda term: ("/remote/", "https://example.org/word.mp3"))
    result = client.post(f"/api/cards/{card_id}/pronunciation").json()
    assert result["audio_url"] == "https://example.org/word.mp3"
    assert result["transcription"] == "/manual/"


def test_answer_normalization_variants_and_ambiguity(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Words", "en", "ru")
    database.add_cards(1, folder, [("fir-tree", "ёлка, ель; ёлочка")])
    session = client.post("/api/study", json={"folder_id": folder, "mode": "all", "direction": "fwd", "study_format": "typing"}).json()
    sid = session["id"]
    assert client.post(f"/api/study/{sid}/check", json={"answer": "  ЁЛКА!!!  "}).json()["verdict"] == "correct"
    assert client.post(f"/api/study/{sid}/check", json={"answer": "елачка"}).json()["verdict"] == "close"
    assert client.post(f"/api/study/{sid}/check", json={"answer": ""}).json()["verdict"] == "empty"


def test_double_answer_unfinished_and_repeat_errors(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Words")
    database.add_cards(1, folder, [("one", "один"), ("two", "два")])
    session = client.post("/api/study", json={"folder_id": folder, "mode": "all", "study_format": "cards"}).json()
    sid = session["id"]
    first_card = session["card_id"]
    first = client.post(f"/api/study/{sid}/answer", json={"success": False, "card_id": first_card})
    assert first.status_code == 200
    assert client.post(f"/api/study/{sid}/answer", json={"success": False, "card_id": first_card}).status_code == 409
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM study_events WHERE session_id=?", (sid,)).fetchone()[0] == 1
    unfinished = client.get(f"/api/study/unfinished?folder_id={folder}").json()
    assert unfinished["id"] == sid
    while not first.json()["done"]:
        first = client.post(f"/api/study/{sid}/answer", json={"success": True})
    repeated = client.post(f"/api/study/{sid}/repeat-errors")
    assert repeated.status_code == 201 and repeated.json()["total"] == 1


def test_simple_round_is_fixed_unique_and_retries_only_unknown(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Round")
    database.add_cards(1, folder, [(f"word-{i}", f"слово-{i}") for i in range(15)])

    session = client.post("/api/study", json={"folder_id": folder, "mode": "due", "study_format": "cards"}).json()
    assert session["total"] == 10
    sid = session["id"]
    seen = []
    unknown = set()
    while not session["done"]:
        seen.append(session["card_id"])
        success = len(seen) > 3
        if not success:
            unknown.add(session["card_id"])
        session = client.post(f"/api/study/{sid}/answer", json={"success": success, "card_id": seen[-1]}).json()

    assert len(seen) == len(set(seen)) == 10
    assert session["known"] == 7
    assert session["unknown"] == session["errors"] == 3

    repeated = client.post(f"/api/study/{sid}/repeat-errors").json()
    assert repeated["total"] == 3
    repeated_seen = []
    while not repeated["done"]:
        repeated_seen.append(repeated["card_id"])
        repeated = client.post(
            f"/api/study/{repeated['id']}/answer",
            json={"success": True, "card_id": repeated["card_id"]},
        ).json()
    assert set(repeated_seen) == unknown
    assert repeated["known"] == 3 and repeated["unknown"] == 0


def test_study_can_pause_or_finish_without_reappearing(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Stop")
    database.add_cards(1, folder, [("one", "один"), ("two", "два")])
    session = client.post("/api/study", json={"folder_id": folder, "mode": "due"}).json()
    sid = session["id"]
    assert client.get(f"/api/study/unfinished?folder_id={folder}").json()["id"] == sid
    assert client.post(f"/api/study/{sid}/finish").json()["ok"] is True
    assert client.get(f"/api/study/unfinished?folder_id={folder}").json() == {"id": None}


def test_weekly_stats_definitions_and_user_independence(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Shared")
    database.add_cards(1, folder, [("one", "один")])
    card_id = database.cards(folder)[0]["id"]
    token = database.create_invite(1, folder, "member")
    database.join(2, token)
    database.save_session("owner-session", 1, folder, "all:fwd", [card_id])
    database.answer("owner-session", card_id, True, "all:fwd")
    database.advance("owner-session")
    owner = database.weekly_stats(1, -180)
    student = database.weekly_stats(2, -180)
    assert owner["trainings"] == 1 and owner["unique_words"] == 1 and owner["accuracy"] == 100
    assert student["trainings"] == 0 and student["unique_words"] == 0 and student["accuracy"] == 0


def test_study_time_uses_active_seconds_not_wall_clock(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Timing")
    database.add_cards(1, folder, [("time", "время")])
    card_id = database.cards(folder)[0]["id"]
    database.save_session("timed", 1, folder, "all:fwd", [card_id])
    database.answer("timed", card_id, True, "all:fwd")
    database.advance("timed")
    with database.conn() as con:
        con.execute("UPDATE sessions SET started_at='2020-01-01T00:00:00+00:00',completed_at=CURRENT_TIMESTAMP,active_seconds=42 WHERE id='timed'")
    assert database.weekly_stats(1)["duration_seconds"] == 42
    result = webapp.session_json(1, "timed")
    assert result["duration_seconds"] == 42


def test_study_payload_does_not_expose_usage_examples(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    folder = database.create_folder(1, "Context", "en", "ru")
    database.add_cards(1, folder, [("journey", "путешествие")])
    card = database.cards(folder)[0]
    assert card["example"] is None and card["example_translation"] is None
    session = client.post("/api/study", json={"folder_id": folder, "mode": "all"}).json()
    assert "example" not in session and "example_translation" not in session
    assert session["detail_term"] == "journey"


def test_member_roles_invite_revoke_and_owner_protection(tmp_path, monkeypatch):
    database, client = setup_app(tmp_path, monkeypatch)
    monkeypatch.setenv("BOT_USERNAME", "LangSlovo_Bot")
    folder = database.create_folder(1, "Shared")
    invite = client.post(f"/api/folders/{folder}/invites", json={"role": "member"}).json()
    database.join(2, invite["token"])
    assert client.patch(f"/api/folders/{folder}/members/2", json={"role": "editor"}).status_code == 200
    assert database.role(2, folder) == "editor"
    assert client.patch(f"/api/folders/{folder}/members/1", json={"role": "member"}).status_code == 409
    assert client.delete(f"/api/folders/{folder}/invites/{invite['token']}").status_code == 200
    database.user(3, "Later")
    assert database.join(3, invite["token"]) is None


def test_frontend_contains_loading_error_long_name_and_keyboard_guards():
    js = Path("web/app.js").read_text()
    css = Path("web/styles.css").read_text()
    html = Path("web/index.html").read_text()
    assert "renderError" in js and "data-action=\"retry\"" in js
    assert "randomRound" in js and "repeatUnknown" in js
    assert "pronunciationDetails:'Произношение'" in js and "learning-details" in css
    assert 'name="example"' not in js and 'name="example_translation"' not in js
    assert "caldera-tokens.css?v=15" in html
    assert "styles.css?v=15" in html and "app.js?v=15" in html
    assert "fonts.googleapis.com" not in html
    assert "· +" not in js
    assert "-webkit-line-clamp: 2" in css
    assert "folder-header h1" in css and "word-break: break-word" in css
    assert "wordLengthClass" in js and "word-very-long" in css
    assert ".folder-header .mini-progress { margin-top: 18px; }" in css
    assert ".study-card > .simple-results { margin-bottom: 18px; }" in css
    assert "Загружаем слова…" in html


def test_redesign_uses_svg_icons_viewport_card_and_gesture_safe_audio():
    js = Path("web/app.js").read_text()
    css = Path("web/styles.css").read_text()
    html = Path("web/index.html").read_text()
    assert "const ICONS=" in js and "data-icon=\"folder\"" in html
    assert "--tg-viewport-stable-height" in css
    assert "min-height: clamp(380px" in css
    assert ".sound-button.is-playing" in css
    speak = js[js.index("async function speak"):js.index("function fallbackSpeech")]
    assert speak.index("audio.play()") < speak.index("await api(")


def test_game_audio_keeps_ios_tap_and_wrong_pair_marks_only_selected_sides():
    js = Path("web/app.js").read_text()
    listening = js[js.index("function playListeningAudio"):js.index("function ensureLetters")]
    assert "speak(q.speech_text,q.source_lang" in listening
    match_tile = js[js.index("function matchTile"):js.index("async function chooseMatch")]
    assert "state.gameWrong?.[side]===item.card_id" in match_tile
    choose_match = js[js.index("async function chooseMatch"):js.index("function renderListenGame")]
    assert "state.gameWrong={term,translation}" in choose_match


def test_games_are_discoverable_from_home_and_learn_sections():
    js = Path("web/app.js").read_text()
    assert 'data-action="play-hub"' in js
    assert "async function renderPlayHub" in js
    assert "personalGameFolders" in js and "slovoGameSets" in js
    assert "else if(a==='play-hub')" in js


def test_caldera_design_tokens_and_accessible_viewport_are_shipped():
    js = Path("web/app.js").read_text()
    css = Path("web/styles.css").read_text()
    tokens = Path("web/caldera-tokens.css").read_text()
    token_spec = Path("web/caldera-tokens.json").read_text()
    html = Path("web/index.html").read_text()
    for token in ("--color-ember: #fc5000", "--color-plasma-violet: #524ae9", "--color-sulfur: #f5f28e", "--color-limestone: #f7f6f2", "--color-pumice: #e2e2df", "--color-obsidian: #070607"):
        assert token in tokens
    assert '"version": "1.0.0"' in token_spec
    assert "radial-gradient(circle, var(--action-bg)" in css
    assert "--caldera-radius-card: 40px" in tokens and "--caldera-radius-pill: 800px" in tokens
    assert "RobotoCondensed-Variable.ttf" in tokens and "Manrope-Variable.ttf" in tokens
    assert "overflow-x: hidden" not in css and "overflow-x: clip" not in css
    assert "box-shadow" not in css and "backdrop-filter" not in css
    assert "user-scalable=no" not in html
    assert "window.scrollTo({top:0,left:0,behavior:'instant'})" in js


def teardown_module():
    webapp.app.dependency_overrides.clear()
