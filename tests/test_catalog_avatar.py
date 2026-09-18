from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import webapp
from catalog_data import CATEGORIES, SETS, cards_for, seed_catalog
from slovo import DB


def setup(tmp_path: Path, monkeypatch, user_id=101):
    database = DB(str(tmp_path / "catalog.db"))
    database.user(101, "One", "https://example.org/telegram-one.jpg")
    database.user(202, "Two")
    monkeypatch.setattr(webapp, "db", database)
    monkeypatch.setattr(webapp, "AVATAR_DIR", tmp_path / "avatars")
    webapp.AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    webapp.app.dependency_overrides[webapp.current_user] = lambda: webapp.TelegramUser(
        id=user_id, first_name="One" if user_id == 101 else "Two"
    )
    return database, TestClient(webapp.app)


def test_catalog_seed_is_complete_exact_and_idempotent(tmp_path):
    database = DB(str(tmp_path / "seed.db"))
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM catalog_categories").fetchone()[0] == 5
        assert con.execute("SELECT COUNT(*) FROM catalog_sets").fetchone()[0] == 12
        assert con.execute("SELECT COUNT(*) FROM catalog_cards").fetchone()[0] == 300
        assert {row[0] for row in con.execute("SELECT slug FROM catalog_categories")} == {x[0] for x in CATEGORIES}
        assert {row[0] for row in con.execute("SELECT slug FROM catalog_sets")} == {x[0] for x in SETS}
        before = dict(con.execute("SELECT card_key,card_id FROM catalog_cards"))
        folder_before = dict(con.execute("SELECT slug,folder_id FROM catalog_sets"))
        for slug, *_ in SETS:
            stored = [(r[0], r[1]) for r in con.execute('''SELECT c.term,c.translation FROM catalog_cards cc
                JOIN cards c ON c.id=cc.card_id WHERE cc.set_slug=? ORDER BY cc.position''', (slug,))]
            assert stored == [(term, translation) for _, term, translation in cards_for(slug)]
            assert len(stored) == 25
        seed_catalog(con)
        assert before == dict(con.execute("SELECT card_key,card_id FROM catalog_cards"))
        assert folder_before == dict(con.execute("SELECT slug,folder_id FROM catalog_sets"))
        assert con.execute("SELECT COUNT(*) FROM catalog_cards").fetchone()[0] == 300


def test_catalog_search_attach_progress_detach_and_copy(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch)
    catalogue = client.get("/api/catalog").json()
    assert len(catalogue["categories"]) == 5 and len(catalogue["sets"]) == 12
    assert not any(item["added"] for item in catalogue["sets"])
    assert [x["slug"] for x in client.get("/api/catalog?q=boarding%20pass").json()["sets"]] == ["airport"]
    assert [x["slug"] for x in client.get("/api/catalog?q=деловые%20письма").json()["sets"]] == ["work-emails"]

    first = client.post("/api/catalog/airport/attach")
    second = client.post("/api/catalog/airport/attach")
    assert first.status_code == second.status_code == 200
    folder_id = first.json()["folder_id"]
    assert second.json()["folder_id"] == folder_id
    assert database.role(101, folder_id) == "member"
    with database.conn() as con:
        assert con.execute("SELECT COUNT(*) FROM memberships WHERE folder_id=? AND user_id=101", (folder_id,)).fetchone()[0] == 1

    card_id = database.catalog_cards("airport")[0]["id"]
    database.save_session("one", 101, folder_id, "all:fwd", [card_id])
    database.answer("one", card_id, True, "all:fwd")
    database.advance("one")
    database.subscribe_set(202, "airport")
    assert database.catalog_set(101, "airport")["learned_count"] == 1
    assert database.catalog_set(202, "airport")["learned_count"] == 0
    assert client.patch(f"/api/cards/{card_id}", json={"term":"x", "translation":"y"}).status_code == 403

    assert client.delete("/api/catalog/airport/attach").status_code == 200
    assert database.role(101, folder_id) is None
    assert client.post("/api/catalog/airport/attach").status_code == 200
    assert database.catalog_set(101, "airport")["learned_count"] == 1

    copy = client.post("/api/catalog/airport/copy")
    assert copy.status_code == 201 and not copy.json()["is_slovo_set"]
    copy_id = copy.json()["id"]
    copy_card = database.cards(copy_id)[0]
    assert client.patch(f"/api/cards/{copy_card['id']}", json={"term":"my airport", "translation":"мой аэропорт"}).status_code == 200
    assert database.catalog_cards("airport")[0]["term"] == "airport"


def image_bytes(fmt="PNG", size=(900, 600), color=(112, 92, 240)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


def test_avatar_upload_replace_invalid_delete_and_user_isolation(tmp_path, monkeypatch):
    database, client = setup(tmp_path, monkeypatch)
    uploaded = client.post("/api/profile/avatar", files={"file": ("photo.png", image_bytes(), "image/png")})
    assert uploaded.status_code == 200 and uploaded.json()["avatar"]["custom"]
    first_key = database.avatar(101)["custom_avatar_key"]
    first_path = webapp.AVATAR_DIR / first_key
    assert first_path.exists()
    with Image.open(first_path) as saved:
        assert saved.size == (512, 512) and saved.format == "WEBP"

    bad = client.post("/api/profile/avatar", files={"file": ("bad.png", b"not an image", "image/png")})
    assert bad.status_code == 422
    assert database.avatar(101)["custom_avatar_key"] == first_key and first_path.exists()

    replaced = client.post("/api/profile/avatar", files={"file": ("photo.jpg", image_bytes("JPEG", (700, 900)), "image/jpeg")})
    assert replaced.status_code == 200
    second_key = database.avatar(101)["custom_avatar_key"]
    assert second_key != first_key and not first_path.exists()
    assert database.avatar(202)["custom_avatar_key"] is None

    deleted = client.delete("/api/profile/avatar")
    assert deleted.status_code == 200
    assert deleted.json()["avatar"]["url"] == "https://example.org/telegram-one.jpg"
    assert database.avatar(101)["custom_avatar_key"] is None
    assert not (webapp.AVATAR_DIR / second_key).exists()


def teardown_module():
    webapp.app.dependency_overrides.clear()
