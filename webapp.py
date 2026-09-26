"""HTTP API and static frontend for the Slovo Telegram Mini App."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import random
import re
import secrets
import sqlite3
import time
import unicodedata
from io import BytesIO
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

from analytics import Analytics, normalize_ref, source_for_ref
from slovo import DB, LANGUAGES, MAX_CARDS_PER_FOLDER, MAX_CARDS_PER_TOPIC

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
AVATAR_DIR = Path(os.getenv("AVATAR_DIR", BASE_DIR / "data" / "avatars"))
AVATAR_DIR.mkdir(parents=True, exist_ok=True)
MAX_AVATAR_BYTES = 10 * 1024 * 1024
db = DB(os.getenv("DATABASE_PATH", "data/slovo.db"))
app = FastAPI(title="Slovo Mini App", docs_url=None, redoc_url=None)


class TelegramUser(BaseModel):
    id: int
    first_name: str = ""
    last_name: str = ""
    username: str | None = None
    language_code: str | None = None
    photo_url: str | None = None

    @property
    def display_name(self) -> str:
        return " ".join(x for x in (self.first_name, self.last_name) if x).strip() or self.username or str(self.id)


def validate_init_data(raw: str, token: str, max_age: int = 86400) -> TelegramUser:
    """Validate Telegram.WebApp.initData using Telegram's HMAC algorithm."""
    try:
        values = dict(parse_qsl(raw, keep_blank_values=True))
        received_hash = values.pop("hash")
        auth_date = int(values.get("auth_date", "0"))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "Invalid Telegram authorization data") from exc
    if abs(int(time.time()) - auth_date) > max_age:
        raise HTTPException(401, "Telegram authorization data has expired")
    check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(401, "Telegram signature is invalid")
    try:
        return TelegramUser.model_validate_json(values["user"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(401, "Telegram user is missing") from exc


def current_user(x_telegram_init_data: str | None = Header(default=None),
                 x_slovo_start_param: str | None = Header(default=None)) -> TelegramUser:
    token = os.getenv("BOT_TOKEN", "")
    if x_telegram_init_data and token:
        user = validate_init_data(x_telegram_init_data, token)
    elif os.getenv("WEBAPP_DEV_MODE") == "1":
        user = TelegramUser(id=int(os.getenv("DEV_TELEGRAM_USER_ID", "1")), first_name=os.getenv("DEV_TELEGRAM_USER_NAME", "Demo"))
    else:
        raise HTTPException(401, "Open Slovo from Telegram")
    start_param=(x_slovo_start_param or "").strip()
    raw_ref = "organic" if start_param.startswith(("inv_","folder_","class_","ref_")) else start_param
    ref = normalize_ref(raw_ref)
    created = db.user(user.id, user.display_name, user.photo_url, ref, source_for_ref(ref))
    if created:
        Analytics(db).safe_track(user.id, "registration_completed", idempotency_key=f"registration:{user.id}")
    if created and start_param.startswith("ref_"):
        db.apply_referral(user.id,start_param[4:])
    return user


def track(user_id: int, event_name: str, metadata: dict | None = None,
          session_id: str | None = None, idempotency_key: str | None = None) -> bool:
    return Analytics(db).safe_track(user_id, event_name, metadata, session_id, idempotency_key)


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    source_lang: str
    target_lang: str
    request_id: str | None = Field(default=None, min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    source_lang: str | None = None
    target_lang: str | None = None


class CardInput(BaseModel):
    term: str = Field(min_length=1, max_length=250)
    translation: str = Field(min_length=1, max_length=500)
    transcription: str | None = Field(default=None, max_length=250)


class CardsCreate(BaseModel):
    items: list[CardInput] = Field(min_length=1, max_length=200)
    topic_id: int | None = None
    skip_duplicates: bool = True
    request_id: str | None = Field(default=None, min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class CardUpdate(CardInput):
    pass


class LocaleUpdate(BaseModel):
    locale: str
    timezone_offset: int = Field(default=0, ge=-840, le=840)


class InviteCreate(BaseModel):
    role: Literal["editor", "member", "viewer"] = "viewer"


class MemberUpdate(BaseModel):
    role: Literal["editor", "member", "viewer"]


class StudyCreate(BaseModel):
    folder_id: int
    mode: Literal["due", "all", "errors"] = "due"
    direction: Literal["fwd", "rev"] = "fwd"
    study_format: Literal["cards", "typing"] = "cards"
    timezone_offset: int = Field(default=0, ge=-840, le=840)
    topic_id: int | None = None
    assignment_id: int | None = None


class StudyAnswer(BaseModel):
    success: bool
    card_id: int | None = None
    answer_text: str | None = Field(default=None, max_length=1000)


class StudyCheck(BaseModel):
    answer: str = Field(default="", max_length=1000)


class GameCreate(BaseModel):
    folder_id: int
    game_type: Literal["match", "listen", "build"]
    card_ids: list[int] | None = Field(default=None, max_length=10)
    topic_id: int | None = None
    assignment_id: int | None = None

class TopicCreate(BaseModel):
    name: str = Field(min_length=1,max_length=100)

class TopicUpdate(BaseModel):
    name: str = Field(min_length=1,max_length=100)

class TopicDelete(BaseModel):
    target_topic_id: int | None = None

class CardMove(BaseModel):
    topic_id: int

class OnboardingUpdate(BaseModel):
    step: int = Field(ge=1,le=5)
    usage_role: Literal['student','teacher','self'] | None = None
    purposes: list[str] | None = Field(default=None,max_length=8)
    languages: list[str] | None = Field(default=None,max_length=12)
    levels: list[str] | None = Field(default=None,max_length=12)
    declared_source: str | None = Field(default=None,max_length=80)
    complete: bool = False

class ClassCreate(BaseModel):
    name: str = Field(min_length=1,max_length=120)
    language: str = Field(default='en',max_length=12)
    level: str | None = Field(default=None,max_length=40)
    description: str | None = Field(default=None,max_length=500)

class ClassUpdate(BaseModel):
    name: str | None = Field(default=None,min_length=1,max_length=120)
    language: str | None = Field(default=None,max_length=12)
    level: str | None = Field(default=None,max_length=40)
    description: str | None = Field(default=None,max_length=500)

class AssignmentCreate(BaseModel):
    title: str = Field(min_length=1,max_length=160)
    folder_id: int
    topic_id: int | None = None
    student_ids: list[int] | None = Field(default=None,max_length=200)
    deadline: str | None = Field(default=None,max_length=40)
    description: str | None = Field(default=None,max_length=1000)


class GameAction(BaseModel):
    event_id: str = Field(min_length=8, max_length=100)
    action: Literal["match", "answer", "unknown", "skip", "check", "hint"]
    card_id: int | None = None
    choice_id: int | None = None
    translation_card_id: int | None = None
    assembled: str | None = Field(default=None, max_length=100)


class AppOpen(BaseModel):
    session_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def require_language(code: str) -> str:
    if code not in LANGUAGES:
        raise HTTPException(422, "Unsupported language")
    return code


def require_folder(user_id: int, folder_id: int):
    folder = db.folder(user_id, folder_id)
    if not folder:
        with db.conn() as con:
            folder=con.execute('''SELECT f.*,'member' role,cs.slug set_slug,cs.category_slug,cs.description,cs.icon set_icon
FROM assignment_recipients ar JOIN assignments a ON a.id=ar.assignment_id JOIN folders f ON f.id=a.folder_id
LEFT JOIN catalog_sets cs ON cs.folder_id=f.id WHERE ar.user_id=? AND a.folder_id=? AND a.active=1 LIMIT 1''',(user_id,folder_id)).fetchone()
    if not folder:
        raise HTTPException(404, "Folder not found")
    if "set_slug" in folder.keys() and folder["set_slug"] and not check_channel(user_id)["subscribed"]:
        track(user_id,"channel_access_blocked",{"folder_id":folder_id},idempotency_key=f"channel-block:{user_id}:{folder_id}:{int(time.time()//3600)}")
        raise HTTPException(403,"channel_subscription_required")
    return folder


def require_editor(user_id: int, folder_id: int):
    folder = require_folder(user_id, folder_id)
    if folder["role"] not in ("owner", "editor"):
        raise HTTPException(403, "Editing is not allowed")
    return folder


def require_owner(user_id: int, folder_id: int):
    folder = require_folder(user_id, folder_id)
    if folder["role"] != "owner":
        raise HTTPException(403, "Owner access required")
    return folder


def preferred_language(user_id:int)->str:
    try:languages=json.loads(db.user_profile(user_id)["learning_languages"] or "[]")
    except (TypeError,ValueError):languages=[]
    return next((code for code in languages if code in LANGUAGES),"en")


def folder_json(user_id: int, folder, counts: dict | None = None) -> dict:
    word_count = int(counts["word_count"] if counts is not None else db.card_count(folder["id"]))
    due_count = int(counts["due_count"] if counts is not None else db.due_count(user_id, folder["id"]))
    learned = int(counts.get("learned_count", 0) if counts is not None else max(0, word_count - due_count))
    keys = set(folder.keys())
    return {
        "id": folder["id"], "name": folder["name"], "role": folder["role"],
        "source_lang": preferred_language(user_id) if ("set_slug" in keys and folder["set_slug"]) else folder["source_lang"], "target_lang": folder["target_lang"],
        "word_count": word_count, "due_count": due_count, "learned_count": learned,
        "progress_percent": round(learned * 100 / word_count) if word_count else 0,
        "is_slovo_set": bool(folder["set_slug"]) if "set_slug" in keys else False,
        "set_slug": folder["set_slug"] if "set_slug" in keys else None,
    }


def card_json(card,user_id:int|None=None,language:str|None=None) -> dict:
    keys=set(card.keys())
    result={key: card[key] for key in ("id", "term", "translation", "transcription", "audio_url")}
    try:result["synonyms"]=json.loads(card["synonyms"] or "[]") if "synonyms" in keys else []
    except (TypeError,ValueError):result["synonyms"]=[]
    language=language or (preferred_language(user_id) if user_id is not None else None)
    if language and language!='en':
        localized=db.catalog_translation(card["id"],language)
        if localized:
            result["term"]=localized["term"];result["transcription"]=None;result["audio_url"]=None
            try:result["synonyms"]=json.loads(localized["synonyms"] or "[]")
            except (TypeError,ValueError):result["synonyms"]=[]
    result["topic_id"]=card["topic_id"] if "topic_id" in keys else None
    return result


def onboarding_json(user_id:int)->dict:
    row=db.user_profile(user_id)
    def values(key):
        try:return json.loads(row[key] or '[]')
        except (TypeError,ValueError):return []
    return {"required":not bool(row["onboarding_completed_at"]),"step":int(row["onboarding_step"] or 0),
            "usage_role":row["usage_role"],"purposes":values("purposes"),
            "languages":values("learning_languages"),"levels":values("levels")}


def check_channel(user_id:int)->dict:
    channel=os.getenv("SLOVO_CHANNEL_ID","").strip(); url=os.getenv("SLOVO_CHANNEL_URL","").strip()
    if not channel or not os.getenv("BOT_TOKEN",""):
        return {"configured":False,"subscribed":True,"url":url}
    try:
        endpoint=f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/getChatMember?chat_id={quote(channel)}&user_id={user_id}"
        with urlopen(Request(endpoint,headers={"User-Agent":"Slovo/1"}),timeout=6) as response:data=json.load(response)
        member=data.get("result",{});status=member.get("status")
        subscribed=status in {"creator","administrator","member"} or (status=="restricted" and member.get("is_member"))
        old=bool(db.user_profile(user_id)["channel_subscribed"]);db.set_channel_state(user_id,subscribed)
        if old!=subscribed:
            track(user_id,"channel_subscription_changed",{"subscribed":subscribed},idempotency_key=f"channel:{user_id}:{int(subscribed)}:{int(time.time()//3600)}")
            if subscribed:
                track(user_id,"channel_subscription_verified",idempotency_key=f"channel-verified:{user_id}:{int(time.time()//3600)}")
                track(user_id,"official_folders_unlocked",idempotency_key=f"official-unlocked:{user_id}:{int(time.time()//3600)}")
            else:track(user_id,"channel_subscription_lost",idempotency_key=f"channel-lost:{user_id}:{int(time.time()//3600)}")
        return {"configured":True,"subscribed":bool(subscribed),"url":url}
    except (HTTPError,URLError,TimeoutError,ValueError,OSError):
        row=db.user_profile(user_id);return {"configured":True,"subscribed":bool(row["channel_subscribed"]),"url":url,"check_error":True}


def avatar_json(user_id: int, name: str = "") -> dict:
    row = db.avatar(user_id)
    custom_key = row["custom_avatar_key"] if row else None
    telegram_url = row["telegram_avatar_url"] if row else None
    return {
        "custom": bool(custom_key),
        "url": f"/media/avatars/{custom_key}" if custom_key else telegram_url,
        "fallback": name,
    }


def catalog_set_json(row,user_id:int|None=None) -> dict:
    learned = int(row["learned_count"] or 0)
    return {
        "slug": row["slug"], "category_slug": row["category_slug"],
        "category_title": row["category_title"], "category_icon": row["category_icon"],
        "title": row["title"], "description": row["description"], "icon": row["icon"],
        "folder_id": row["folder_id"], "word_count": int(row["word_count"]),
        "added": bool(row["added"]), "learned_count": learned,
        "due_count": int(row["due_count"] or 0),
        "progress_percent": round(learned * 100 / int(row["word_count"])) if row["word_count"] else 0,
        "source_lang": preferred_language(user_id) if user_id is not None else "en", "target_lang": "ru",
    }


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
        "media-src 'self' https:; connect-src 'self' https://api.dictionaryapi.dev"
    )
    return response


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/bootstrap")
def bootstrap(user: TelegramUser = Depends(current_user)):
    """Small critical-path payload; folders and profile load independently."""
    channel=check_channel(user.id);profile=db.user_profile(user.id)
    return {
        "user": {"id": user.id, "name": user.display_name, "username": user.username,
                 "avatar": avatar_json(user.id, user.display_name)},
        "locale": db.locale(user.id), "languages": LANGUAGES,"onboarding":onboarding_json(user.id),
        "channel":channel,"referral":{"code":profile["personal_ref_code"],"count":db.referral_count(user.id),
        "url":f"https://t.me/{os.getenv('BOT_USERNAME','LangSlovo_Bot').lstrip('@')}?start=ref_{profile['personal_ref_code']}"},
    }


@app.patch("/api/onboarding")
def update_onboarding(body:OnboardingUpdate,background_tasks:BackgroundTasks,user:TelegramUser=Depends(current_user)):
    before=onboarding_json(user.id)
    if not before["step"]:track(user.id,"onboarding_started",idempotency_key=f"onboarding-start:{user.id}")
    db.save_onboarding(user.id,body.step,body.usage_role,body.purposes,body.languages,body.levels,body.declared_source,body.complete)
    track(user.id,"onboarding_step_completed",{"step":body.step},idempotency_key=f"onboarding-step:{user.id}:{body.step}")
    if body.step==1 and body.usage_role:track(user.id,"user_role_selected",{"role":body.usage_role},idempotency_key=f"role-selected:{user.id}:{body.usage_role}")
    if body.complete:track(user.id,"onboarding_completed",idempotency_key=f"onboarding-complete:{user.id}")
    if body.complete and preferred_language(user.id)!='en' and os.getenv('LANGUAGE_ENRICHMENT_ENABLED','1')=='1':background_tasks.add_task(prepare_official_sets,user.id)
    return onboarding_json(user.id)


@app.post("/api/channel/check")
def channel_check(user:TelegramUser=Depends(current_user)):
    return check_channel(user.id)

@app.post("/api/channel/subscribe-click")
def channel_subscribe_click(user:TelegramUser=Depends(current_user)):
    track(user.id,"channel_subscribe_clicked",idempotency_key=f"channel-click:{user.id}:{int(time.time()//60)}")
    return {"ok":True}


@app.post("/api/analytics/open")
def analytics_open(body: AppOpen, user: TelegramUser = Depends(current_user)):
    track(user.id, "app_open", session_id=body.session_id,
          idempotency_key=f"app-open:{user.id}:{body.session_id}")
    return {"ok": True}


@app.get("/api/home")
def home(user: TelegramUser = Depends(current_user)):
    folders = [folder_json(user.id, row, dict(row)) for row in db.folder_summaries(user.id)]
    due = sum(folder["due_count"] for folder in folders)
    sets = [catalog_set_json(row,user.id) for row in db.catalog_sets(user.id)]
    categories = [dict(row) for row in db.catalog_categories()]
    starters = [item for item in sets if item["slug"] in {"introductions", "airport", "work-emails"}]
    channel=check_channel(user.id)
    for item in starters:item["locked"]=not channel["subscribed"]
    return {"folders": folders, "due_count": due, "estimated_minutes": math.ceil(due / 3) if due else 0,
            "catalog_categories": categories, "starter_sets": starters,"channel":channel}


@app.get("/api/profile")
def profile(timezone_offset: int = Query(0, ge=-840, le=840), user: TelegramUser = Depends(current_user)):
    db.set_timezone(user.id, timezone_offset)
    return {"summary": db.profile_stats(user.id), "week": db.weekly_stats(user.id, timezone_offset),
            "avatar": avatar_json(user.id, user.display_name),"learning_profile":onboarding_json(user.id)}


@app.post("/api/profile/avatar")
async def upload_avatar(file: UploadFile = File(...), user: TelegramUser = Depends(current_user)):
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Use a JPG, PNG or WebP image")
    payload = await file.read(MAX_AVATAR_BYTES + 1)
    if len(payload) > MAX_AVATAR_BYTES:
        raise HTTPException(413, "The image must be no larger than 10 MB")
    try:
        with Image.open(BytesIO(payload)) as source:
            source.verify()
        with Image.open(BytesIO(payload)) as source:
            detected_format = source.format
            source = ImageOps.exif_transpose(source)
            if detected_format not in {"JPEG", "PNG", "WEBP"}:
                raise HTTPException(415, "Use a JPG, PNG or WebP image")
            if source.width < 64 or source.height < 64:
                raise HTTPException(422, "The image is too small")
            if source.width * source.height > 40_000_000:
                raise HTTPException(422, "The image resolution is too large")
            rendered = ImageOps.fit(source.convert("RGB"), (512, 512), method=Image.Resampling.LANCZOS)
            key = f"{user.id}-{secrets.token_hex(12)}.webp"
            target = AVATAR_DIR / key
            rendered.save(target, "WEBP", quality=88, method=6)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(422, "The image could not be decoded") from exc
    old = db.avatar(user.id)
    old_key = old["custom_avatar_key"] if old else None
    try:
        db.set_custom_avatar(user.id, key)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if old_key and old_key != key:
        (AVATAR_DIR / Path(old_key).name).unlink(missing_ok=True)
    return {"ok": True, "avatar": avatar_json(user.id, user.display_name)}


@app.delete("/api/profile/avatar")
def delete_avatar(user: TelegramUser = Depends(current_user)):
    old = db.avatar(user.id)
    old_key = old["custom_avatar_key"] if old else None
    db.set_custom_avatar(user.id, None)
    if old_key:
        (AVATAR_DIR / Path(old_key).name).unlink(missing_ok=True)
    return {"ok": True, "avatar": avatar_json(user.id, user.display_name)}


@app.patch("/api/profile/locale")
def update_locale(body: LocaleUpdate, user: TelegramUser = Depends(current_user)):
    db.set_locale(user.id, require_language(body.locale)); db.set_timezone(user.id, body.timezone_offset)
    return {"ok": True, "locale": body.locale}


@app.get("/api/catalog")
def catalog(category: str = Query("", max_length=40), q: str = Query("", max_length=100),
            user: TelegramUser = Depends(current_user)):
    categories = [dict(row) for row in db.catalog_categories()]
    allowed = {item["slug"] for item in categories}
    if category and category not in allowed:
        raise HTTPException(422, "Unknown category")
    channel=check_channel(user.id);sets=[catalog_set_json(row,user.id) for row in db.catalog_sets(user.id, category, q.strip())]
    for item in sets:item["locked"]=not channel["subscribed"]
    return {"categories": categories,"sets":sets,"channel":channel}


@app.get("/api/catalog/{slug}")
def catalog_detail(slug: str, user: TelegramUser = Depends(current_user)):
    row = db.catalog_set(user.id, slug)
    if not row:
        raise HTTPException(404, "Slovo set not found")
    result = catalog_set_json(row,user.id)
    result["locked"]=not check_channel(user.id)["subscribed"]
    if result["locked"]:track(user.id,"channel_access_blocked",{"folder_id":result["folder_id"]},idempotency_key=f"channel-block:{user.id}:{result['folder_id']}:{int(time.time()//3600)}")
    else:track(user.id,"official_folder_opened",{"folder_id":result["folder_id"]},idempotency_key=f"official-open:{user.id}:{result['folder_id']}:{int(time.time()//60)}")
    language=preferred_language(user.id);ensure_catalog_localization(slug,language)
    result["source_lang"]=language
    result["cards"] = [card_json(card,user.id,language) for card in db.catalog_cards(slug)]
    return result


@app.post("/api/catalog/{slug}/attach")
def attach_catalog_set(slug: str, user: TelegramUser = Depends(current_user)):
    if not check_channel(user.id)["subscribed"]:
        row=db.catalog_set(user.id,slug)
        if row:track(user.id,"channel_access_blocked",{"folder_id":row["folder_id"]},idempotency_key=f"channel-block:{user.id}:{row['folder_id']}:{int(time.time()//3600)}")
        raise HTTPException(403,"channel_subscription_required")
    ensure_catalog_localization(slug,preferred_language(user.id))
    folder_id = db.subscribe_set(user.id, slug)
    if not folder_id:
        raise HTTPException(404, "Slovo set not found")
    return {"ok": True, "folder_id": folder_id,
            "set": catalog_set_json(db.catalog_set(user.id, slug),user.id)}


@app.delete("/api/catalog/{slug}/attach")
def detach_catalog_set(slug: str, user: TelegramUser = Depends(current_user)):
    if not db.unsubscribe_set(user.id, slug):
        raise HTTPException(404, "Slovo set not found")
    return {"ok": True}


@app.post("/api/catalog/{slug}/copy", status_code=201)
def copy_catalog_set(slug: str, user: TelegramUser = Depends(current_user)):
    if not check_channel(user.id)["subscribed"]:raise HTTPException(403,"channel_subscription_required")
    language=preferred_language(user.id);ensure_catalog_localization(slug,language)
    folder_id = db.copy_catalog_set(user.id, slug)
    if not folder_id:
        raise HTTPException(404, "Slovo set not found")
    db.localize_catalog_copy(folder_id,slug,language)
    track(user.id, "shared_folder_copied", {"folder_id": folder_id},
          idempotency_key=f"catalog-copy:{user.id}:{folder_id}")
    return folder_json(user.id, db.folder(user.id, folder_id))


@app.post("/api/folders", status_code=201)
def create_folder(body: FolderCreate, user: TelegramUser = Depends(current_user)):
    cached = db.request_result(user.id, "folder_create", body.request_id)
    if cached:
        folder_id = int(cached); return folder_json(user.id, db.folder(user.id, folder_id))
    source, target = require_language(body.source_lang), require_language(body.target_lang)
    if source == target:
        raise HTTPException(422, "Choose two different languages")
    folder_id = db.create_folder(user.id, body.name.strip(), source, target)
    db.save_request_result(user.id, "folder_create", body.request_id, str(folder_id))
    track(user.id, "folder_created", {"folder_id": folder_id},
          idempotency_key=f"folder-created:{folder_id}")
    return folder_json(user.id, db.folder(user.id, folder_id))


@app.get("/api/folders/{folder_id}")
def get_folder(folder_id: int, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
               q: str = Query("", max_length=100), filter: Literal["all", "due", "errors"] = "all",
               topic_id: int | None = Query(default=None),
               user: TelegramUser = Depends(current_user)):
    folder = require_folder(user.id, folder_id)
    cards = db.cards(folder_id, offset, 10000, q.strip(), filter, user.id)
    if topic_id is not None:cards=[card for card in cards if card["topic_id"]==topic_id]
    result = folder_json(user.id, folder)
    members = []
    if folder["role"] == "owner":
        for item in db.members(folder_id):
            member = dict(item)
            member["avatar"] = {"url": f"/media/avatars/{member['custom_avatar_key']}" if member["custom_avatar_key"] else member["telegram_avatar_url"]}
            member.pop("custom_avatar_key", None); member.pop("telegram_avatar_url", None)
            members.append(member)
    result.update({"cards": [card_json(card,user.id) for card in cards[:limit]], "offset": offset, "has_more": len(cards) > limit,
                   "members": members,"topics":[{**dict(row),"learned_count":int(row["learned_count"] or 0)} for row in db.topics(user.id,folder_id)]})
    return result


@app.post("/api/folders/{folder_id}/topics",status_code=201)
def create_topic(folder_id:int,body:TopicCreate,user:TelegramUser=Depends(current_user)):
    require_editor(user.id,folder_id)
    try:topic_id=db.create_topic(user.id,folder_id,body.name.strip())
    except sqlite3.IntegrityError as exc:raise HTTPException(409,"topic_name_exists") from exc
    return dict(db.topic(user.id,topic_id))


@app.patch("/api/topics/{topic_id}")
def update_topic(topic_id:int,body:TopicUpdate,user:TelegramUser=Depends(current_user)):
    topic=db.topic(user.id,topic_id)
    if not topic:raise HTTPException(404,"Topic not found")
    require_editor(user.id,topic["folder_id"])
    try:db.rename_topic(topic_id,body.name.strip())
    except sqlite3.IntegrityError as exc:raise HTTPException(409,"topic_name_exists") from exc
    return dict(db.topic(user.id,topic_id))


@app.post("/api/topics/{topic_id}/delete")
def delete_topic(topic_id:int,body:TopicDelete,user:TelegramUser=Depends(current_user)):
    topic=db.topic(user.id,topic_id)
    if not topic:raise HTTPException(404,"Topic not found")
    require_editor(user.id,topic["folder_id"])
    try:db.delete_topic(topic_id,body.target_topic_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return {"ok":True}


@app.patch("/api/cards/{card_id}/topic")
def move_card(card_id:int,body:CardMove,user:TelegramUser=Depends(current_user)):
    card=db.card(user.id,card_id)
    if not card:raise HTTPException(404,"Card not found")
    require_editor(user.id,card["folder_id"])
    try:db.move_card(card_id,body.topic_id)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    return {"ok":True,"topic_id":body.topic_id}


@app.patch("/api/folders/{folder_id}")
def update_folder(folder_id: int, body: FolderUpdate, user: TelegramUser = Depends(current_user)):
    folder = require_owner(user.id, folder_id)
    if body.name is not None:
        db.rename(folder_id, body.name.strip())
    source = require_language(body.source_lang or folder["source_lang"])
    target = require_language(body.target_lang or folder["target_lang"])
    if source == target:
        raise HTTPException(422, "Choose two different languages")
    db.set_folder_languages(folder_id, source, target)
    return folder_json(user.id, db.folder(user.id, folder_id))


@app.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: int, user: TelegramUser = Depends(current_user)):
    require_owner(user.id, folder_id); db.delete_folder(folder_id)
    return {"ok": True}


@app.post("/api/folders/{folder_id}/cards", status_code=201)
def create_cards(folder_id: int, body: CardsCreate, background_tasks:BackgroundTasks,user: TelegramUser = Depends(current_user)):
    require_editor(user.id, folder_id)
    topic_id=body.topic_id
    if topic_id is not None:
        topic=db.topic(user.id,topic_id)
        if not topic or topic["folder_id"]!=folder_id:raise HTTPException(422,"invalid_topic")
    cached = db.request_result(user.id, f"cards_create:{folder_id}", body.request_id)
    if cached: return json.loads(cached)
    items = [(x.term.strip(), x.translation.strip(), clean_optional(x.transcription)) for x in body.items]
    duplicates = [item for item in items if db.duplicate(folder_id, item[0], item[1])]
    accepted = [item for item in items if not (body.skip_duplicates and db.duplicate(folder_id, item[0], item[1]))]
    if accepted:
        try: inserted_ids = db.add_cards(user.id, folder_id, accepted,topic_id)
        except ValueError as exc:
            if str(exc) in {"topic_word_limit","invalid_topic"}: raise HTTPException(409, str(exc)) from exc
            raise
        if os.getenv('LANGUAGE_ENRICHMENT_ENABLED','1')=='1':background_tasks.add_task(enrich_card_synonyms,inserted_ids)
        method = "single" if len(accepted) == 1 else "bulk"
        track(user.id, "word_added", {"folder_id": folder_id, "words_count": len(accepted), "input_method": method},
              idempotency_key=f"word-added:{user.id}:{folder_id}:{inserted_ids[0]}:{inserted_ids[-1]}")
    response = {"added": len(accepted), "duplicates": len(duplicates)}
    db.save_request_result(user.id, f"cards_create:{folder_id}", body.request_id, json.dumps(response))
    return response


def clean_optional(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@app.patch("/api/cards/{card_id}")
def update_card(card_id: int, body: CardUpdate, user: TelegramUser = Depends(current_user)):
    card = db.card(user.id, card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    require_editor(user.id, card["folder_id"])
    values = {"term": body.term.strip(), "translation": body.translation.strip(), "transcription": clean_optional(body.transcription)}
    for field, value in values.items():
        db.update_card(card_id, field, value)
    return {"ok": True, "card": card_json(db.card(user.id, card_id),user.id)}


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: int, user: TelegramUser = Depends(current_user)):
    card = db.card(user.id, card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    require_editor(user.id, card["folder_id"]); db.delete_card(card_id)
    return {"ok": True}


def translate_texts(values:list[str],source:str,target:str)->list[str]:
    """Best-effort public translation with a short timeout; callers always retain a safe fallback."""
    if not values or source==target:return values
    endpoint=os.getenv("TRANSLATION_API_URL","https://api.mymemory.translated.net/get")
    joined="\n".join(values)
    url=f"{endpoint}?{urlencode({'q':joined,'langpair':f'{source}|{target}'})}"
    try:
        with urlopen(Request(url,headers={"User-Agent":"Slovo/1.0"}),timeout=8) as response:data=json.load(response)
        translated=str(data.get("responseData",{}).get("translatedText") or "").strip()
        rows=translated.splitlines()
        if len(rows)==len(values) and all(row.strip() for row in rows):return [row.strip() for row in rows]
    except (HTTPError,URLError,TimeoutError,ValueError,OSError):pass
    return values


def dictionary_details(term:str,language:str='en')->tuple[str|None,str|None,list[str]]:
    """Return pronunciation and a small deduplicated synonym list."""
    request = Request(f"https://api.dictionaryapi.dev/api/v2/entries/{quote(language)}/{quote(term)}", headers={"User-Agent": "Slovo/1.0"})
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310 - fixed HTTPS host
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        return None,None,[]
    if not isinstance(data, list) or not data:
        return None,None,[]
    entry = data[0]; transcription = entry.get("phonetic") or None; audio = None
    synonyms=[]
    for phonetic in entry.get("phonetics", []):
        transcription = transcription or phonetic.get("text") or None
        if phonetic.get("audio"):
            audio = phonetic["audio"]
            if audio.startswith("//"): audio = "https:" + audio
            break
    for meaning in entry.get("meanings",[]):
        synonyms.extend(meaning.get("synonyms") or [])
        for definition in meaning.get("definitions",[]):synonyms.extend(definition.get("synonyms") or [])
    clean=[];seen={term.casefold()}
    for value in synonyms:
        value=str(value).strip()
        if value and value.casefold() not in seen:seen.add(value.casefold());clean.append(value)
        if len(clean)>=6:break
    return transcription,audio,clean


def dictionary_lookup(term: str) -> tuple[str | None, str | None]:
    transcription,audio,_=dictionary_details(term,'en')
    return transcription,audio


def synonym_lookup(term:str,language:str)->list[str]:
    _,_,synonyms=dictionary_details(term,language)
    if synonyms or language=='en':return synonyms
    english=translate_texts([term],language,'en')[0]
    _,_,english_synonyms=dictionary_details(english,'en')
    return translate_texts(english_synonyms,'en',language) if english_synonyms else []


def enrich_card_synonyms(card_ids:list[int])->None:
    for card_id in card_ids:
        with db.conn() as con:row=con.execute("SELECT c.term,f.source_lang FROM cards c JOIN folders f ON f.id=c.folder_id WHERE c.id=?",(card_id,)).fetchone()
        if not row:continue
        synonyms=synonym_lookup(row["term"],row["source_lang"])
        db.update_card(card_id,"synonyms",json.dumps(synonyms,ensure_ascii=False))


def ensure_catalog_localization(slug:str,language:str)->None:
    if language=='en':return
    cards=db.catalog_cards(slug);missing=[card for card in cards if not db.catalog_translation(card["id"],language)]
    if not missing:return
    originals=[card["term"] for card in missing];translated=translate_texts(originals,'en',language)
    if translated==originals:return
    for card,term in zip(missing,translated):
        try:base_synonyms=json.loads(card["synonyms"] or '[]')
        except (TypeError,ValueError):base_synonyms=[]
        synonyms=translate_texts(base_synonyms,'en',language) if base_synonyms else []
        db.save_catalog_translation(card["id"],language,term,synonyms)


def prepare_official_sets(user_id:int)->None:
    language=preferred_language(user_id)
    if language=='en':return
    for row in db.catalog_sets(user_id):ensure_catalog_localization(row["slug"],language)


def enrich_folder_pronunciations(folder_id: int) -> None:
    """Fill English phonetics/audio without delaying the Mini App response."""
    with db.conn() as con:
        folder = con.execute("SELECT source_lang FROM folders WHERE id=?", (folder_id,)).fetchone()
        if not folder or folder["source_lang"] != "en": return
        cards = con.execute('''SELECT id,term,transcription,audio_url FROM cards
WHERE folder_id=? AND (transcription IS NULL OR trim(transcription)='' OR audio_url IS NULL OR trim(audio_url)='')
ORDER BY id LIMIT 50''', (folder_id,)).fetchall()
    for card in cards:
        if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,80}", card["term"]): continue
        cache_key=card["term"].strip().casefold()
        with db.conn() as con:
            cached=con.execute("SELECT transcription,audio_url FROM pronunciation_cache WHERE cache_key=?",(cache_key,)).fetchone()
        if cached:
            transcription,audio=cached["transcription"],cached["audio_url"]
        else:
            transcription,audio=dictionary_lookup(card["term"])
            with db.conn() as con:
                con.execute("INSERT OR REPLACE INTO pronunciation_cache(cache_key,transcription,audio_url,updated_at) VALUES(?,?,?,CURRENT_TIMESTAMP)",(cache_key,transcription,audio))
        with db.conn() as con:
            con.execute('''UPDATE cards SET
transcription=COALESCE(NULLIF(trim(transcription),''),?),
audio_url=COALESCE(NULLIF(trim(audio_url),''),?) WHERE id=?''',(transcription,audio,card["id"]))


@app.post("/api/folders/{folder_id}/enrich", status_code=202)
def enrich_folder(folder_id: int, background_tasks: BackgroundTasks, user: TelegramUser = Depends(current_user)):
    require_folder(user.id, folder_id)
    background_tasks.add_task(enrich_folder_pronunciations, folder_id)
    return {"accepted": True}


@app.post("/api/cards/{card_id}/pronunciation")
def pronunciation(card_id: int, user: TelegramUser = Depends(current_user)):
    card = db.card(user.id, card_id)
    if not card:
        raise HTTPException(404, "Card not found")
    folder = require_folder(user.id, card["folder_id"])
    if folder["source_lang"] != "en":
        return {"available": False, "transcription": card["transcription"], "audio_url": card["audio_url"]}
    transcription, audio = card["transcription"], card["audio_url"]
    # A manually entered transcription must not prevent fetching missing audio.
    if not audio and re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,80}", card["term"]):
        found_transcription, found_audio = dictionary_lookup(card["term"])
        if not transcription and found_transcription:
            transcription = found_transcription
            db.update_card(card_id, "transcription", transcription)
        audio = found_audio
        if audio:
            db.update_card(card_id, "audio_url", audio)
    return {"available": bool(audio or folder["source_lang"] in LANGUAGES), "transcription": transcription, "audio_url": audio,
            "speech_lang": folder["source_lang"]}


@app.post("/api/folders/{folder_id}/invites")
def create_invite(folder_id: int, body: InviteCreate, user: TelegramUser = Depends(current_user)):
    require_owner(user.id, folder_id)
    username = os.getenv("BOT_USERNAME", "").lstrip("@")
    if not username:
        raise HTTPException(503, "BOT_USERNAME is not configured")
    stored_role="member" if body.role=="viewer" else body.role
    token = db.create_invite(user.id, folder_id, stored_role)
    track(user.id, "share_link_created", {"folder_id": folder_id},
          idempotency_key=f"share-created:{token}")
    return {"token": token, "url": f"https://t.me/{username}?start={token}", "role": "viewer" if stored_role=="member" else stored_role}


@app.get("/api/folders/{folder_id}/invites")
def list_invites(folder_id: int, user: TelegramUser = Depends(current_user)):
    require_owner(user.id, folder_id)
    username = os.getenv("BOT_USERNAME", "").lstrip("@")
    return {"items": [{**dict(row), "url": f"https://t.me/{username}?start={row['token']}"} for row in db.invites(folder_id)]}


@app.delete("/api/folders/{folder_id}/invites/{token}")
def revoke_invite(folder_id: int, token: str, user: TelegramUser = Depends(current_user)):
    require_owner(user.id, folder_id)
    if not db.revoke_invite(folder_id, token): raise HTTPException(404, "Invitation not found")
    return {"ok": True}


@app.post("/api/invites/{token}/join")
def join_invite(token: str, user: TelegramUser = Depends(current_user)):
    if not token.startswith(("inv_","folder_")) or len(token) > 80:
        raise HTTPException(404, "Invitation not found")
    folder_id = db.join(user.id, token)
    if not folder_id:
        raise HTTPException(404, "This invitation is invalid or has been revoked")
    track(user.id, "shared_folder_opened", {"folder_id": folder_id},
          idempotency_key=f"shared-open:{user.id}:{token}")
    return {"ok": True, "folder_id": folder_id}


@app.get("/api/invites/{token}")
def invite_preview(token:str,user:TelegramUser=Depends(current_user)):
    if not token.startswith(("inv_","folder_")) or len(token)>80:raise HTTPException(404,"Invitation not found")
    row=db.invite_preview(user.id,token)
    if not row or row["revoked"]:raise HTTPException(404,"Invitation not found")
    return dict(row)


@app.post("/api/invites/{token}/decline")
def decline_invite(token:str,user:TelegramUser=Depends(current_user)):
    if not db.decline_invite(user.id,token):raise HTTPException(404,"Invitation not found")
    return {"ok":True}


@app.patch("/api/folders/{folder_id}/members/{member_id}")
def update_member(folder_id: int, member_id: int, body: MemberUpdate, user: TelegramUser = Depends(current_user)):
    require_owner(user.id, folder_id)
    role="member" if body.role=="viewer" else body.role
    if member_id == user.id or not db.set_member_role(folder_id, member_id, role):
        raise HTTPException(409, "The owner role cannot be changed")
    return {"ok": True}


@app.delete("/api/folders/{folder_id}/members/{member_id}")
def delete_member(folder_id: int, member_id: int, user: TelegramUser = Depends(current_user)):
    require_owner(user.id, folder_id)
    if member_id == user.id: raise HTTPException(409, "The owner cannot be removed")
    db.remove_member(folder_id, member_id)
    return {"ok": True}


def require_teacher(user_id:int):
    row=db.user_profile(user_id)
    if not row or row["usage_role"]!="teacher":raise HTTPException(403,"Teacher profile required")
    return row


def require_class_teacher(user_id:int,class_id:int):
    with db.conn() as con:row=con.execute("SELECT * FROM classes WHERE id=? AND teacher_user_id=?",(class_id,user_id)).fetchone()
    if not row:raise HTTPException(404,"Class not found")
    return row


@app.get("/api/classes")
def list_classes(user:TelegramUser=Depends(current_user)):
    with db.conn() as con:
        owned=[dict(row) for row in con.execute("SELECT c.*,(SELECT COUNT(*) FROM class_members cm WHERE cm.class_id=c.id AND cm.status='active') student_count FROM classes c WHERE teacher_user_id=? ORDER BY active DESC,updated_at DESC",(user.id,))]
        joined=[dict(row) for row in con.execute("SELECT c.* FROM classes c JOIN class_members cm ON cm.class_id=c.id WHERE cm.user_id=? AND cm.status='active' ORDER BY c.active DESC,c.updated_at DESC",(user.id,))]
        assignments=[dict(row) for row in con.execute('''SELECT a.*,c.name class_name,f.name folder_name,t.name topic_name,ar.status
FROM assignment_recipients ar JOIN assignments a ON a.id=ar.assignment_id JOIN classes c ON c.id=a.class_id
JOIN folders f ON f.id=a.folder_id LEFT JOIN topics t ON t.id=a.topic_id WHERE ar.user_id=? AND a.active=1 ORDER BY a.deadline IS NULL,a.deadline,a.id DESC''',(user.id,))]
    return {"owned":owned,"joined":joined,"assignments":assignments,"teacher":db.user_profile(user.id)["usage_role"]=="teacher"}


@app.post("/api/classes",status_code=201)
def create_class(body:ClassCreate,user:TelegramUser=Depends(current_user)):
    require_teacher(user.id);code="class_"+secrets.token_urlsafe(12)
    with db.conn() as con:
        cur=con.execute("INSERT INTO classes(teacher_user_id,name,language,level,description,invite_code) VALUES(?,?,?,?,?,?)",(user.id,body.name.strip(),body.language,body.level,body.description,code));class_id=cur.lastrowid
    track(user.id,"class_created",{"class_id":class_id},idempotency_key=f"class-created:{class_id}")
    return {"id":class_id,"invite_code":code,"url":f"https://t.me/{os.getenv('BOT_USERNAME','LangSlovo_Bot').lstrip('@')}?start={code}"}


@app.get("/api/classes/{class_id}")
def class_detail(class_id:int,user:TelegramUser=Depends(current_user)):
    classroom=require_class_teacher(user.id,class_id)
    with db.conn() as con:
        members=[dict(row) for row in con.execute("SELECT u.telegram_id,u.name,cm.status,cm.joined_at FROM class_members cm JOIN users u ON u.telegram_id=cm.user_id WHERE cm.class_id=? AND cm.status='active' ORDER BY u.name",(class_id,))]
        assignments=[dict(row) for row in con.execute("SELECT a.*,f.name folder_name,t.name topic_name FROM assignments a JOIN folders f ON f.id=a.folder_id LEFT JOIN topics t ON t.id=a.topic_id WHERE a.class_id=? ORDER BY a.id DESC",(class_id,))]
        progress=[dict(row) for row in con.execute('''SELECT ar.assignment_id,ar.user_id,u.name,ar.status,ar.started_at,ar.completed_at,
COUNT(DISTINCT cd.id) total_count,
COUNT(DISTINCT CASE WHEN p.mastery_status='mastered' THEN cd.id END) known_words,
COUNT(DISTINCT CASE WHEN COALESCE(p.mastery_status,'new')!='mastered' THEN cd.id END) unknown_words,
COUNT(DISTINCT CASE WHEN p.mastery_status='mastered' THEN cd.id END) learned_words,
COUNT(DISTINCT CASE WHEN COALESCE(p.mastery_status,'new')!='mastered' AND COALESCE(p.error_count,0)>0 THEN cd.id END) repeat_words,
COUNT(DISTINCT CASE WHEN COALESCE(p.mastery_status,'new')!='mastered' AND COALESCE(p.error_count,0)=0 THEN cd.id END) learning_words,
ROUND(100.0*SUM(CASE WHEN e.correct=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(e.id),0)) accuracy,
ROUND(100.0*COUNT(DISTINCT CASE WHEN p.mastery_status='mastered' THEN cd.id END)/NULLIF(COUNT(DISTINCT cd.id),0)) completion_percent
FROM assignment_recipients ar JOIN assignments a ON a.id=ar.assignment_id JOIN users u ON u.telegram_id=ar.user_id
JOIN cards cd ON cd.folder_id=a.folder_id AND (a.topic_id IS NULL OR cd.topic_id=a.topic_id)
LEFT JOIN progress p ON p.card_id=cd.id AND p.user_id=ar.user_id
LEFT JOIN study_events e ON e.card_id=cd.id AND e.user_id=ar.user_id WHERE a.class_id=? GROUP BY ar.assignment_id,ar.user_id''',(class_id,))]
    return {**dict(classroom),"members":members,"assignments":assignments,"progress":progress,
            "url":f"https://t.me/{os.getenv('BOT_USERNAME','LangSlovo_Bot').lstrip('@')}?start={classroom['invite_code']}"}


@app.patch("/api/classes/{class_id}")
def update_class(class_id:int,body:ClassUpdate,user:TelegramUser=Depends(current_user)):
    classroom=require_class_teacher(user.id,class_id)
    values={"name":body.name.strip() if body.name else classroom["name"],"language":body.language or classroom["language"],
            "level":body.level if body.level is not None else classroom["level"],"description":body.description if body.description is not None else classroom["description"]}
    with db.conn() as con:con.execute("UPDATE classes SET name=?,language=?,level=?,description=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(values["name"],values["language"],values["level"],values["description"],class_id))
    return {"ok":True,**values}


@app.delete("/api/classes/{class_id}/members/{student_id}")
def remove_class_student(class_id:int,student_id:int,user:TelegramUser=Depends(current_user)):
    require_class_teacher(user.id,class_id)
    with db.conn() as con:con.execute("UPDATE class_members SET status='removed',updated_at=CURRENT_TIMESTAMP WHERE class_id=? AND user_id=?",(class_id,student_id))
    return {"ok":True}


@app.post("/api/classes/join/{code}")
def join_class(code:str,user:TelegramUser=Depends(current_user)):
    if not code.startswith("class_"):raise HTTPException(404,"Class invitation not found")
    with db.conn() as con:
        classroom=con.execute("SELECT * FROM classes WHERE invite_code=? AND active=1",(code,)).fetchone()
        if not classroom:raise HTTPException(404,"Class invitation not found")
        if classroom["teacher_user_id"]==user.id:raise HTTPException(409,"Teacher is already in this class")
        con.execute("INSERT INTO class_members(class_id,user_id,status) VALUES(?,?,'active') ON CONFLICT(class_id,user_id) DO UPDATE SET status='active',updated_at=CURRENT_TIMESTAMP",(classroom["id"],user.id))
    track(user.id,"class_joined",{"class_id":classroom["id"]},idempotency_key=f"class-joined:{classroom['id']}:{user.id}")
    return {"ok":True,"class_id":classroom["id"]}


@app.post("/api/classes/{class_id}/regenerate-link")
def regenerate_class_link(class_id:int,user:TelegramUser=Depends(current_user)):
    require_class_teacher(user.id,class_id);code="class_"+secrets.token_urlsafe(12)
    with db.conn() as con:con.execute("UPDATE classes SET invite_code=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(code,class_id))
    return {"code":code,"url":f"https://t.me/{os.getenv('BOT_USERNAME','LangSlovo_Bot').lstrip('@')}?start={code}"}


@app.post("/api/classes/{class_id}/deactivate")
def deactivate_class(class_id:int,user:TelegramUser=Depends(current_user)):
    require_class_teacher(user.id,class_id)
    with db.conn() as con:con.execute("UPDATE classes SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",(class_id,))
    return {"ok":True}


@app.post("/api/classes/{class_id}/assignments",status_code=201)
def create_assignment(class_id:int,body:AssignmentCreate,user:TelegramUser=Depends(current_user)):
    require_class_teacher(user.id,class_id);require_folder(user.id,body.folder_id)
    if body.topic_id is not None:
        topic=db.topic(user.id,body.topic_id)
        if not topic or topic["folder_id"]!=body.folder_id:raise HTTPException(422,"invalid_topic")
    with db.conn() as con:
        members=[row[0] for row in con.execute("SELECT user_id FROM class_members WHERE class_id=? AND status='active'",(class_id,))]
        recipients=members if body.student_ids is None else [value for value in body.student_ids if value in set(members)]
        if not recipients:raise HTTPException(422,"No valid recipients")
        cur=con.execute("INSERT INTO assignments(class_id,created_by,title,folder_id,topic_id,scope,deadline,description) VALUES(?,?,?,?,?,?,?,?)",(class_id,user.id,body.title.strip(),body.folder_id,body.topic_id,"all" if body.student_ids is None else "selected",body.deadline,body.description));assignment_id=cur.lastrowid
        con.executemany("INSERT INTO assignment_recipients(assignment_id,user_id) VALUES(?,?)",[(assignment_id,value) for value in recipients])
    track(user.id,"assignment_created",{"assignment_id":assignment_id,"class_id":class_id},idempotency_key=f"assignment-created:{assignment_id}")
    track(user.id,"assignment_assigned",{"assignment_id":assignment_id,"class_id":class_id,"recipients":len(recipients)},idempotency_key=f"assignment-assigned:{assignment_id}")
    return {"id":assignment_id,"recipients":len(recipients)}


def safe_json(value: str, fallback):
    try: return json.loads(value)
    except (TypeError, ValueError): return fallback


def session_json(user_id: int, session_id: str) -> dict:
    session = db.session(user_id, session_id)
    if not session: raise HTTPException(404, "Study session not found")
    require_folder(user_id, session["folder_id"])
    queue = safe_json(session["queue"], []); unique_ids = list(dict.fromkeys(queue)); errors = set(safe_json(session["errors"], []))
    with db.conn() as con:
        stats = con.execute("SELECT COUNT(DISTINCT card_id) unique_done,COUNT(*) attempts,SUM(first_attempt) first_correct FROM study_events WHERE session_id=?", (session_id,)).fetchone()
    total = len(unique_ids)
    if not session["current_card"]:
        duration = session["active_seconds"] or min(1800, (stats["attempts"] or 0) * 15)
        return {"id": session_id, "folder_id": session["folder_id"], "done": True, "total": total,
                "unique_done": stats["unique_done"] or 0, "attempts": stats["attempts"] or 0,
                "errors": len(errors), "first_correct": stats["first_correct"] or 0,
                "correct": stats["first_correct"] or 0,
                "known": stats["first_correct"] or 0, "unknown": len(errors),
                "accuracy": round((stats["first_correct"] or 0)*100/total) if total else 0,
                "duration_seconds": duration, "review_due": len(errors)}
    card = db.card(user_id, session["current_card"])
    if not card:
        db.advance(session_id); return session_json(user_id, session_id)
    folder = require_folder(user_id, session["folder_id"]); localized=card_json(card,user_id);reverse = session["mode"].endswith(":rev")
    source_lang=preferred_language(user_id) if folder["is_official"] else folder["source_lang"]
    front_lang = folder["target_lang"] if reverse else source_lang
    return {"id": session_id, "folder_id": session["folder_id"], "done": False,
            "position": session["pos"] + 1, "total": total, "attempts": stats["attempts"] or 0,
            "card_id": card["id"], "front": localized["translation"] if reverse else localized["term"],
            "back": localized["term"] if reverse else localized["translation"], "front_lang": front_lang,
            "transcription": None if reverse else localized["transcription"], "audio_url": None if reverse else localized["audio_url"],
            "details_transcription": localized["transcription"], "detail_term": localized["term"],
            "detail_lang": source_lang, "detail_audio_url": localized["audio_url"],"synonyms":localized["synonyms"],
            "study_format": session["study_format"], "mode": session["mode"].split(":",1)[0]}


@app.get("/api/study/unfinished")
def unfinished_study(folder_id: int,topic_id:int|None=None, user: TelegramUser = Depends(current_user)):
    require_folder(user.id, folder_id)
    if topic_id is None:raise HTTPException(422,"topic_required")
    topic=db.topic(user.id,topic_id)
    if not topic or topic["folder_id"]!=folder_id:raise HTTPException(422,"invalid_topic")
    row = db.unfinished_session(user.id, folder_id,topic_id)
    return session_json(user.id, row["id"]) if row else {"id": None}


@app.post("/api/study", status_code=201)
def create_study(body: StudyCreate, user: TelegramUser = Depends(current_user)):
    require_folder(user.id, body.folder_id); db.set_timezone(user.id, body.timezone_offset)
    if body.topic_id is None and body.assignment_id is None:raise HTTPException(422,"topic_required")
    if body.topic_id is not None:
        topic=db.topic(user.id,body.topic_id)
        if not topic or topic["folder_id"]!=body.folder_id:raise HTTPException(422,"invalid_topic")
    if body.assignment_id is not None:
        with db.conn() as con:
            recipient=con.execute("SELECT a.topic_id FROM assignment_recipients ar JOIN assignments a ON a.id=ar.assignment_id WHERE ar.assignment_id=? AND ar.user_id=? AND a.folder_id=? AND a.active=1",(body.assignment_id,user.id,body.folder_id)).fetchone()
            if not recipient:raise HTTPException(403,"Assignment not available")
            if recipient["topic_id"] is not None and body.topic_id!=recipient["topic_id"]:raise HTTPException(403,"Assignment topic mismatch")
            con.execute("UPDATE assignment_recipients SET status='started',started_at=COALESCE(started_at,CURRENT_TIMESTAMP) WHERE assignment_id=? AND user_id=?",(body.assignment_id,user.id))
        track(user.id,"assignment_opened",{"assignment_id":body.assignment_id},idempotency_key=f"assignment-open:{body.assignment_id}:{user.id}")
        track(user.id,"assignment_started",{"assignment_id":body.assignment_id},idempotency_key=f"assignment-started:{body.assignment_id}:{user.id}")
    mode = f"{body.mode}:{body.direction}"; card_ids = db.candidates(user.id, body.folder_id, mode,body.topic_id)[:10]
    if not card_ids:
        return {"done": True, "total": 0, "errors": 0, "first_correct": 0, "accuracy": 0, "folder_id": body.folder_id}
    session_id = secrets.token_urlsafe(12)
    db.save_session(session_id, user.id, body.folder_id, mode, card_ids, body.study_format, body.timezone_offset,body.topic_id,body.assignment_id)
    track(user.id, "test_started", {"test_session_id": session_id, "folder_id": body.folder_id, "words_count": len(card_ids)},
          session_id=session_id, idempotency_key=f"test-started:{session_id}")
    return session_json(user.id, session_id)


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = re.sub(r"[^\w\s'-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def answer_variants(value: str) -> list[str]:
    return [item for item in (normalize_answer(x) for x in re.split(r"[,;\n]+", value)) if item]


@app.post("/api/study/{session_id}/check")
def check_study(session_id: str, body: StudyCheck, user: TelegramUser = Depends(current_user)):
    session = db.session(user.id, session_id)
    if not session or not session["current_card"]: raise HTTPException(409, "This study session has ended")
    card = db.card(user.id, session["current_card"])
    if not card: raise HTTPException(409, "This word is no longer available")
    localized=card_json(card,user.id);reverse = session["mode"].endswith(":rev"); correct = localized["term"] if reverse else localized["translation"]
    given = normalize_answer(body.answer); variants = answer_variants(correct)
    if not given: return {"verdict": "empty", "correct_answer": correct}
    if given in variants: return {"verdict": "correct", "correct_answer": correct}
    similarity = max((SequenceMatcher(None, given, variant).ratio() for variant in variants), default=0)
    verdict = "close" if similarity >= 0.72 else "wrong"
    return {"verdict": verdict, "correct_answer": correct, "similarity": round(similarity, 2)}


@app.post("/api/study/{session_id}/answer")
def answer_study(session_id: str, body: StudyAnswer, user: TelegramUser = Depends(current_user)):
    session = db.session(user.id, session_id)
    if not session or not session["current_card"]: raise HTTPException(409, "This study session has ended")
    if body.card_id is not None and body.card_id != session["current_card"]:
        raise HTTPException(409, "Answer already recorded")
    require_folder(user.id, session["folder_id"])
    if not db.answer(session_id, session["current_card"], body.success, session["mode"], body.answer_text):
        raise HTTPException(409, "Answer already recorded")
    db.advance(session_id)
    result = session_json(user.id, session_id)
    if result["done"]:
        track(user.id, "test_completed", {"test_session_id": session_id, "folder_id": result["folder_id"],
              "words_count": result["total"], "known_count": result["known"], "unknown_count": result["unknown"]},
              session_id=session_id, idempotency_key=f"test-completed:{session_id}")
        if session["assignment_id"]:
            with db.conn() as con:con.execute("UPDATE assignment_recipients SET status='completed',completed_at=CURRENT_TIMESTAMP WHERE assignment_id=? AND user_id=?",(session["assignment_id"],user.id))
            track(user.id,"assignment_completed",{"assignment_id":session["assignment_id"]},idempotency_key=f"assignment-complete:{session['assignment_id']}:{user.id}")
    return result


@app.post("/api/study/{session_id}/repeat-errors", status_code=201)
def repeat_errors(session_id: str, user: TelegramUser = Depends(current_user)):
    old = db.session(user.id, session_id)
    if not old: raise HTTPException(404, "Study session not found")
    errors = list(dict.fromkeys(safe_json(old["errors"], [])))[:10]
    if not errors: return {"done": True, "total": 0, "folder_id": old["folder_id"], "errors": 0, "first_correct": 0, "accuracy": 0}
    new_id = secrets.token_urlsafe(12)
    db.save_session(new_id, user.id, old["folder_id"], f"errors:{'rev' if old['mode'].endswith(':rev') else 'fwd'}", errors, old["study_format"], old["timezone_offset"])
    track(user.id, "test_started", {"test_session_id": new_id, "folder_id": old["folder_id"], "words_count": len(errors)},
          session_id=new_id, idempotency_key=f"test-started:{new_id}")
    return session_json(user.id, new_id)


@app.post("/api/study/{session_id}/finish")
def finish_study(session_id: str, user: TelegramUser = Depends(current_user)):
    session = db.session(user.id, session_id)
    if not session: raise HTTPException(404, "Study session not found")
    require_folder(user.id, session["folder_id"])
    db.stop_session(session_id)
    return {"ok": True, "folder_id": session["folder_id"]}


def _game_cards(user_id: int, folder_id: int,topic_id:int|None=None) -> list[dict]:
    require_folder(user_id, folder_id)
    rows=db.cards(folder_id, limit=10000, user_id=user_id)
    if topic_id is not None:rows=[row for row in rows if row["topic_id"]==topic_id]
    return [card_json(row,user_id) for row in rows]


def _unambiguous(cards: list[dict]) -> list[dict]:
    """Conservative text-level ambiguity filter; this intentionally makes no semantic claim."""
    term_counts: dict[str, int] = {}
    variants: dict[int, set[str]] = {}
    for card in cards:
        key = normalize_answer(card["term"])
        term_counts[key] = term_counts.get(key, 0) + 1
        variants[card["id"]] = set(answer_variants(card["translation"]))
    result = []
    used: set[str] = set()
    for card in cards:
        current = variants[card["id"]]
        if not current or term_counts.get(normalize_answer(card["term"]), 0) != 1 or current & used:
            continue
        result.append(card); used |= current
    return result


def _game_snapshot(user_id: int, folder_id: int, game_type: str, requested: list[int] | None = None,topic_id:int|None=None) -> tuple[dict, dict]:
    folder = require_folder(user_id, folder_id)
    source_lang=preferred_language(user_id) if folder["is_official"] else folder["source_lang"]
    cards = _game_cards(user_id, folder_id,topic_id)
    if requested is not None:
        requested_set = set(requested)
        cards = [card for card in cards if card["id"] in requested_set]
    rng = random.SystemRandom()
    if game_type == "match":
        eligible = _unambiguous(cards); rng.shuffle(eligible); selected = eligible[:6]
        if len(selected) < 2: raise HTTPException(409, "match_needs_two")
        terms = [card["id"] for card in selected]; translations = terms.copy(); rng.shuffle(terms); rng.shuffle(translations)
        return {"cards": selected, "term_order": terms, "translation_order": translations}, {"matched": [], "mistakes": [], "wrong_connections": 0}
    if game_type == "listen":
        eligible = _unambiguous(cards)
        if len(eligible) < 4: raise HTTPException(409, "listen_needs_four")
        rng.shuffle(eligible); selected = eligible[:10]; questions = []
        for card in selected:
            wrong = [other for other in eligible if other["id"] != card["id"]]
            choices = rng.sample(wrong, 3) + [card]; rng.shuffle(choices)
            questions.append({**card, "choices": [{"card_id": item["id"], "translation": item["translation"]} for item in choices]})
        return {"cards": questions, "source_lang": source_lang}, {"pos": 0, "responses": {}, "correct": 0, "wrong": 0, "unknown": 0, "skipped": 0, "mistakes": []}
    if source_lang != "en": raise HTTPException(409, "build_english_only")
    eligible = [card for card in cards if re.fullmatch(r"[A-Za-z]{3,12}", card["term"])]
    rng.shuffle(eligible); selected = eligible[:10]
    if not selected: raise HTTPException(409, "build_no_words")
    return {"cards": selected, "source_lang": source_lang}, {"pos": 0, "responses": {}, "had_error": [], "hints": [], "first": 0, "helped": 0, "unknown": 0, "mistakes": []}


def _active_tick(row, now) -> int:
    active = int(row["active_seconds"] or 0)
    if row["status"] != "in_progress": return active
    try:
        previous = time.mktime(time.strptime(row["updated_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        current = time.mktime(time.strptime(now[:19], "%Y-%m-%dT%H:%M:%S"))
        return active + max(0, min(60, round(current - previous)))
    except (TypeError, ValueError): return active


def _game_result(game_type: str, snapshot: dict, state: dict) -> dict:
    total = len(snapshot["cards"])
    if game_type == "match":
        return {"total": total, "matched": len(state["matched"]), "wrong_connections": state["wrong_connections"], "mistakes": len(set(state["mistakes"]))}
    if game_type == "listen":
        denominator = state["correct"] + state["wrong"] + state["unknown"]
        return {"total": total, "correct": state["correct"], "wrong": state["wrong"], "unknown": state["unknown"],
                "skipped": state["skipped"], "accuracy": round(state["correct"] * 100 / denominator) if denominator else None,
                "mistakes": len(set(state["mistakes"]))}
    return {"total": total, "first": state["first"], "helped": state["helped"], "unknown": state["unknown"], "mistakes": len(set(state["mistakes"]))}


def game_json(user_id: int, round_id: str, resume: bool = False) -> dict:
    row = db.game_round(user_id, round_id)
    if not row: raise HTTPException(404, "Game round not found")
    folder = require_folder(user_id, row["folder_id"])
    snapshot = safe_json(row["snapshot"], {"cards": []}); state = safe_json(row["state"], {})
    if resume and row["status"] == "paused":
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        with db.conn() as con: con.execute("UPDATE game_rounds SET status='in_progress',updated_at=? WHERE id=?", (now, round_id))
        row = db.game_round(user_id, round_id)
    done = row["status"] in ("completed", "abandoned")
    result = {"id": round_id, "folder_id": row["folder_id"], "folder_name": folder["name"], "game_type": row["game_type"],
              "status": row["status"], "done": done, "duration_seconds": int(row["active_seconds"] or 0),
              "total": len(snapshot["cards"]), "result": _game_result(row["game_type"], snapshot, state)}
    if row["game_type"] == "match":
        by_id = {card["id"]: card for card in snapshot["cards"]}
        result.update({"terms": [{"card_id": cid, "text": by_id[cid]["term"]} for cid in snapshot["term_order"]],
                       "translations": [{"card_id": cid, "text": by_id[cid]["translation"]} for cid in snapshot["translation_order"]],
                       "matched_ids": state["matched"], "collected": len(state["matched"])})
    else:
        pos = int(state["pos"]); result["completed"] = pos; result["position"] = min(pos + 1, len(snapshot["cards"]))
        if pos < len(snapshot["cards"]) and not done:
            card = snapshot["cards"][pos]
            if row["game_type"] == "listen":
                # The text is kept out of the rendered question, but is sent as
                # a dedicated speech value so iOS can use SpeechSynthesis during
                # the original tap when a recorded pronunciation is unavailable.
                result["question"] = {"card_id": card["id"], "audio_url": card.get("audio_url"), "speech_text": card["term"],
                                      "choices": card["choices"], "source_lang": snapshot["source_lang"]}
            else:
                result["question"] = {"card_id": card["id"], "translation": card["translation"], "term": card["term"], "source_lang": snapshot["source_lang"],
                                      "had_error": card["id"] in state["had_error"], "used_hint": card["id"] in state["hints"]}
        if pos and str(pos - 1) in state.get("responses", {}): result["last_response"] = state["responses"][str(pos - 1)]
    return result


@app.get("/api/games/unfinished")
def unfinished_game(folder_id: int,topic_id:int|None=None, game_type: Literal["match", "listen", "build"] | None = None, user: TelegramUser = Depends(current_user)):
    require_folder(user.id, folder_id)
    if topic_id is None:raise HTTPException(422,"topic_required")
    row = db.unfinished_game(user.id, folder_id, game_type,topic_id)
    return game_json(user.id, row["id"]) if row else {"id": None}


@app.get("/api/games/options")
def game_options(folder_id: int,topic_id:int|None=None, user: TelegramUser = Depends(current_user)):
    folder = require_folder(user.id, folder_id)
    if topic_id is None:raise HTTPException(422,"topic_required")
    topic=db.topic(user.id,topic_id)
    if not topic or topic["folder_id"]!=folder_id:raise HTTPException(422,"invalid_topic")
    cards = _game_cards(user.id, folder_id,topic_id); clear = _unambiguous(cards)
    build = [card for card in cards if re.fullmatch(r"[A-Za-z]{3,12}", card["term"])] if folder["source_lang"] == "en" else []
    return {"folder_id": folder_id, "folder_name": folder["name"], "games": {
        "match": {"size": min(6,len(clear)), "available": len(clear)>=2, "reason": None if len(clear)>=2 else "match_needs_two"},
        "listen": {"size": min(10,len(clear)), "available": len(clear)>=4, "reason": None if len(clear)>=4 else "listen_needs_four"},
        "build": {"size": min(10,len(build)), "available": bool(build), "reason": None if build else ("build_english_only" if folder["source_lang"]!="en" else "build_no_words")},
    }}


@app.post("/api/games", status_code=201)
def create_game(body: GameCreate, user: TelegramUser = Depends(current_user)):
    if body.topic_id is None and body.assignment_id is None:raise HTTPException(422,"topic_required")
    if body.topic_id is not None:
        topic=db.topic(user.id,body.topic_id)
        if not topic or topic["folder_id"]!=body.folder_id:raise HTTPException(422,"invalid_topic")
    snapshot, state = _game_snapshot(user.id, body.folder_id, body.game_type, body.card_ids,body.topic_id)
    round_id = secrets.token_urlsafe(12); db.create_game_round(round_id, user.id, body.folder_id, body.game_type, snapshot, state,body.topic_id,body.assignment_id)
    track(user.id, "game_started", {"game_session_id": round_id, "game_type": body.game_type, "folder_id": body.folder_id},
          session_id=round_id, idempotency_key=f"game-started:{round_id}")
    return game_json(user.id, round_id)


@app.get("/api/games/{round_id}")
def get_game(round_id: str, user: TelegramUser = Depends(current_user)):
    return game_json(user.id, round_id, resume=True)


@app.post("/api/games/{round_id}/action")
def game_action(round_id: str, body: GameAction, user: TelegramUser = Depends(current_user)):
    row = db.game_round(user.id, round_id)
    if not row: raise HTTPException(404, "Game round not found")
    require_folder(user.id, row["folder_id"])
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with db.conn() as con:
        cached = con.execute("SELECT response FROM game_events WHERE round_id=? AND event_id=?", (round_id, body.event_id)).fetchone()
        if cached:
            cached_response = safe_json(cached["response"], {})
            current = game_json(user.id, round_id)
            current.update(cached_response)
            return current
        # Serialize actions for this SQLite database. After waiting for another
        # action to commit, re-check the event id before reading round state.
        # This prevents two rapid taps with different requests from advancing
        # the same question twice, while preserving idempotent retries.
        con.execute("BEGIN IMMEDIATE")
        cached = con.execute("SELECT response FROM game_events WHERE round_id=? AND event_id=?", (round_id, body.event_id)).fetchone()
        if cached:
            cached_response = safe_json(cached["response"], {})
            current = game_json(user.id, round_id)
            current.update(cached_response)
            return current
        locked = con.execute("SELECT * FROM game_rounds WHERE id=? AND user_id=?", (round_id, user.id)).fetchone()
        if not locked or locked["status"] != "in_progress": raise HTTPException(409, "This game round has ended")
        snapshot = safe_json(locked["snapshot"], {"cards": []}); state = safe_json(locked["state"], {})
        cards = snapshot["cards"]; by_id = {card["id"]: card for card in cards}; game_type = locked["game_type"]
        reveal = None
        if game_type == "match":
            if body.action != "match" or body.card_id not in by_id or body.translation_card_id not in by_id: raise HTTPException(422, "Invalid pair")
            if body.card_id in state["matched"]: raise HTTPException(409, "Pair already collected")
            if body.card_id == body.translation_card_id:
                state["matched"].append(body.card_id); outcome = "correct"
            else:
                state["wrong_connections"] += 1
                if body.card_id not in state["mistakes"]: state["mistakes"].append(body.card_id)
                outcome = "wrong"
            completed = len(state["matched"]) == len(cards)
        else:
            pos = int(state["pos"])
            if pos >= len(cards): raise HTTPException(409, "Question already completed")
            card = cards[pos]
            if body.card_id != card["id"]: raise HTTPException(409, "Question already completed")
            if game_type == "listen":
                if body.action not in ("answer", "unknown", "skip"): raise HTTPException(422, "Invalid action")
                if body.action == "answer":
                    allowed = {choice["card_id"] for choice in card["choices"]}
                    if body.choice_id not in allowed: raise HTTPException(422, "Invalid answer")
                    outcome = "correct" if body.choice_id == card["id"] else "wrong"; state[outcome] += 1
                    if outcome == "wrong": state["mistakes"].append(card["id"])
                elif body.action == "unknown": outcome = "unknown"; state["unknown"] += 1; state["mistakes"].append(card["id"])
                else: outcome = "skipped"; state["skipped"] += 1
                reveal = {"term": card["term"], "translation": card["translation"], "transcription": card.get("transcription"), "selected_id": body.choice_id, "outcome": outcome}
                state["responses"][str(pos)] = reveal; state["pos"] += 1
            else:
                if body.action == "hint":
                    if card["id"] not in state["hints"]: state["hints"].append(card["id"])
                    outcome = "hint"; completed = False
                elif body.action == "check":
                    if body.assembled is None: raise HTTPException(422, "Missing word")
                    if unicodedata.normalize("NFKC", body.assembled).casefold() == unicodedata.normalize("NFKC", card["term"]).casefold():
                        helped = card["id"] in state["hints"] or card["id"] in state["had_error"]
                        outcome = "helped" if helped else "first"; state[outcome] += 1
                        if helped: state["mistakes"].append(card["id"])
                        reveal = {"term": card["term"], "outcome": outcome}; state["responses"][str(pos)] = reveal; state["pos"] += 1
                    else:
                        if card["id"] not in state["had_error"]: state["had_error"].append(card["id"])
                        outcome = "wrong"; state["mistakes"].append(card["id"])
                elif body.action == "unknown":
                    outcome = "unknown"; state["unknown"] += 1; state["mistakes"].append(card["id"])
                    reveal = {"term": card["term"], "outcome": outcome}; state["responses"][str(pos)] = reveal; state["pos"] += 1
                else: raise HTTPException(422, "Invalid action")
            completed = int(state["pos"]) >= len(cards)
        active = _active_tick(locked, now); status = "completed" if completed else "in_progress"
        con.execute("UPDATE game_rounds SET state=?,status=?,active_seconds=?,updated_at=?,completed_at=CASE WHEN ?='completed' THEN ? ELSE completed_at END WHERE id=?",
                    (json.dumps(state,ensure_ascii=False),status,active,now,status,now,round_id))
        response = {"outcome": outcome, "reveal": reveal, "done": completed}
        con.execute("INSERT INTO game_events(round_id,event_id,action,card_id,payload,response) VALUES(?,?,?,?,?,?)",
                    (round_id,body.event_id,body.action,body.card_id,body.model_dump_json(),json.dumps(response,ensure_ascii=False)))
    result = game_json(user.id, round_id); result["outcome"] = outcome; result["reveal"] = reveal
    if result["done"] and result["status"] == "completed":
        track(user.id, "game_completed", {"game_session_id": round_id, "game_type": result["game_type"],
              "folder_id": result["folder_id"], "result_summary": result["result"]},
              session_id=round_id, idempotency_key=f"game-completed:{round_id}")
    return result


@app.post("/api/games/{round_id}/pause")
def pause_game(round_id: str, user: TelegramUser = Depends(current_user)):
    row = db.game_round(user.id, round_id)
    if not row: raise HTTPException(404, "Game round not found")
    require_folder(user.id, row["folder_id"]); now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with db.conn() as con: con.execute("UPDATE game_rounds SET status='paused',active_seconds=?,updated_at=? WHERE id=? AND status='in_progress'", (_active_tick(row, now),now,round_id))
    return {"ok": True, "folder_id": row["folder_id"]}


@app.post("/api/games/{round_id}/finish")
def finish_game(round_id: str, user: TelegramUser = Depends(current_user)):
    row = db.game_round(user.id, round_id)
    if not row: raise HTTPException(404, "Game round not found")
    require_folder(user.id, row["folder_id"]); now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with db.conn() as con: con.execute("UPDATE game_rounds SET status='abandoned',active_seconds=?,updated_at=?,completed_at=? WHERE id=? AND status IN ('in_progress','paused')", (_active_tick(row,now),now,now,round_id))
    return {"ok": True, "folder_id": row["folder_id"]}


@app.post("/api/games/{round_id}/repeat", status_code=201)
def repeat_game(round_id: str, errors_only: bool = False, user: TelegramUser = Depends(current_user)):
    old = db.game_round(user.id, round_id)
    if not old: raise HTTPException(404, "Game round not found")
    require_folder(user.id, old["folder_id"]); snapshot = safe_json(old["snapshot"], {"cards": []}); state = safe_json(old["state"], {})
    ids = list(dict.fromkeys(state.get("mistakes", []))) if errors_only else [card["id"] for card in snapshot["cards"]]
    if old["game_type"] == "match" and len(ids) == 1:
        neutral = next((card["id"] for card in snapshot["cards"] if card["id"] not in ids), None)
        if neutral is not None: ids.append(neutral)
    new_snapshot,new_state = _game_snapshot(user.id,old["folder_id"],old["game_type"],ids)
    new_id=secrets.token_urlsafe(12);db.create_game_round(new_id,user.id,old["folder_id"],old["game_type"],new_snapshot,new_state)
    track(user.id, "game_started", {"game_session_id": new_id, "game_type": old["game_type"], "folder_id": old["folder_id"]},
          session_id=new_id, idempotency_key=f"game-started:{new_id}")
    return game_json(user.id,new_id)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
app.mount("/media/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")
