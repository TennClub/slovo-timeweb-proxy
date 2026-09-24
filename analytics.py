"""First-party product analytics for Slovo.

All stored timestamps are UTC. Calendar reports are converted to the configured
IANA timezone (Europe/Moscow by default). Analytics failures are intentionally
isolated from product actions by ``safe_track``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

EVENTS = {
    "app_open", "registration_completed", "folder_created", "word_added",
    "test_started", "test_completed", "game_started", "game_completed",
    "share_link_created", "shared_folder_opened", "shared_folder_copied",
    "assignment_opened", "assignment_completed",
    "onboarding_started","onboarding_step_completed","onboarding_completed",
    "channel_subscription_changed","class_created","class_joined","assignment_created",
}
MEANINGFUL_EVENTS = {
    "folder_created", "word_added", "test_started", "test_completed",
    "game_started", "game_completed", "shared_folder_copied",
    "assignment_completed",
    "onboarding_completed","class_joined",
}
REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
EVENT_FIELDS = {
    "app_open": set(),
    "registration_completed": set(),
    "folder_created": {"folder_id"},
    "word_added": {"folder_id", "words_count", "input_method"},
    "test_started": {"test_session_id", "folder_id", "words_count"},
    "test_completed": {"test_session_id", "folder_id", "words_count", "known_count", "unknown_count"},
    "game_started": {"game_session_id", "game_type", "folder_id"},
    "game_completed": {"game_session_id", "game_type", "folder_id", "result_summary"},
    "share_link_created": {"folder_id"},
    "shared_folder_opened": {"folder_id"},
    "shared_folder_copied": {"folder_id"},
    "assignment_opened": {"assignment_id"},
    "assignment_completed": {"assignment_id"},
    "onboarding_started": set(),
    "onboarding_step_completed": {"step"},
    "onboarding_completed": set(),
    "channel_subscription_changed": {"subscribed"},
    "class_created": {"class_id"},
    "class_joined": {"class_id"},
    "assignment_created": {"assignment_id","class_id"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat()


def parse_excluded(value: str | None = None) -> set[int]:
    result = set()
    for raw in (value if value is not None else os.getenv("ANALYTICS_EXCLUDED_TELEGRAM_IDS", "")).split(","):
        try:
            if raw.strip(): result.add(int(raw.strip()))
        except ValueError:
            logger.warning("Ignoring an invalid excluded analytics id")
    return result


def normalize_ref(value: str | None) -> str:
    value = (value or "").strip()
    return value if REF_RE.fullmatch(value) else "organic"


def source_for_ref(ref_code: str) -> str:
    if ref_code == "organic": return "organic"
    if ref_code.startswith("tutor_"): return "tutor"
    if ref_code.startswith("school_"): return "school"
    if ref_code.startswith("telegram_"): return "telegram"
    if ref_code.startswith("friend"): return "friend"
    return "campaign"


def validate_metadata(event_name: str, metadata: dict | None) -> dict:
    if event_name not in EVENTS: raise ValueError("Unknown analytics event")
    value = dict(metadata or {})
    unexpected = set(value) - EVENT_FIELDS[event_name]
    if unexpected: raise ValueError("Unexpected analytics metadata")
    if event_name == "word_added":
        value["words_count"] = max(1, min(200, int(value.get("words_count", 1))))
        if value.get("input_method") not in {"single", "bulk", "import", "unknown"}: value["input_method"] = "unknown"
    if event_name == "test_completed":
        total = int(value["words_count"]); known = int(value["known_count"]); unknown = int(value["unknown_count"])
        if total < 0 or known < 0 or unknown < 0 or known + unknown != total:
            raise ValueError("Invalid completed test totals")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > 4096: raise ValueError("Analytics metadata is too large")
    return value


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


class Analytics:
    def __init__(self, db, timezone_name: str | None = None, excluded: set[int] | None = None):
        self.db = db
        self.timezone_name = timezone_name or os.getenv("ANALYTICS_TIMEZONE", "Europe/Moscow")
        try: self.tz = ZoneInfo(self.timezone_name)
        except Exception:
            logger.warning("Invalid ANALYTICS_TIMEZONE; using Europe/Moscow")
            self.timezone_name = "Europe/Moscow"; self.tz = ZoneInfo(self.timezone_name)
        self.excluded = parse_excluded() if excluded is None else set(excluded)

    def track(self, user_id: int, event_name: str, metadata: dict | None = None,
              session_id: str | None = None, idempotency_key: str | None = None,
              created_at: datetime | None = None) -> bool:
        value = validate_metadata(event_name, metadata)
        with self.db.conn() as con:
            try:
                con.execute("""INSERT INTO analytics_events(user_id,event_name,created_at,metadata,session_id,idempotency_key)
VALUES(?,?,?,?,?,?)""", (user_id, event_name, iso_utc(created_at), json.dumps(value, ensure_ascii=False), session_id, idempotency_key))
                return True
            except sqlite3.IntegrityError as exc:
                if idempotency_key and "idempotency_key" in str(exc): return False
                raise

    def safe_track(self, *args, **kwargs) -> bool:
        try: return self.track(*args, **kwargs)
        except Exception:
            logger.exception("Analytics event could not be stored")
            return False

    def attribute(self, user_id: int, ref_code: str | None, campaign: str | None = None) -> bool:
        ref = normalize_ref(ref_code); source = source_for_ref(ref)
        with self.db.conn() as con:
            row = con.execute("SELECT acquired_at FROM users WHERE telegram_id=?", (user_id,)).fetchone()
            if not row or row["acquired_at"]: return False
            con.execute("UPDATE users SET acquisition_source=?,campaign=?,ref_code=?,acquired_at=? WHERE telegram_id=? AND acquired_at IS NULL",
                        (source, (campaign or ref)[:64], ref, iso_utc(), user_id))
            return bool(con.total_changes)

    def _excluded_sql(self, column="u.telegram_id") -> tuple[str, list[int]]:
        # Non-positive IDs are reserved for built-in catalogue/system records.
        if not self.excluded: return f" AND {column}>0", []
        marks = ",".join("?" for _ in self.excluded)
        return f" AND {column}>0 AND {column} NOT IN ({marks})", sorted(self.excluded)

    def _day_window(self, day: date, cutoff: time | None = None) -> Window:
        start = datetime.combine(day, time.min, self.tz)
        end = datetime.combine(day + timedelta(days=1), time.min, self.tz) if cutoff is None else datetime.combine(day, cutoff, self.tz)
        return Window(start.astimezone(timezone.utc), end.astimezone(timezone.utc))

    def _scalar(self, sql: str, params=()) -> int:
        with self.db.conn() as con: return int(con.execute(sql, params).fetchone()[0] or 0)

    def period(self, start: datetime, end: datetime) -> dict:
        exclusion, excluded = self._excluded_sql("u.telegram_id")
        meaningful = ",".join("?" for _ in MEANINGFUL_EVENTS)
        base = [iso_utc(start), iso_utc(end)]
        with self.db.conn() as con:
            total = con.execute(f"SELECT COUNT(*) FROM users u WHERE 1=1{exclusion}", excluded).fetchone()[0]
            new = con.execute(f"SELECT COUNT(*) FROM users u WHERE datetime(u.created_at)>=datetime(?) AND datetime(u.created_at)<datetime(?){exclusion}", base + excluded).fetchone()[0]
            active = con.execute(f"""SELECT COUNT(DISTINCT e.user_id) FROM analytics_events e JOIN users u ON u.telegram_id=e.user_id
WHERE e.created_at>=? AND e.created_at<? AND e.event_name IN ({meaningful}){exclusion}""", base + sorted(MEANINGFUL_EVENTS) + excluded).fetchone()[0]
            counts = {row["event_name"]: row["n"] for row in con.execute(f"""SELECT e.event_name,COUNT(*) n FROM analytics_events e JOIN users u ON u.telegram_id=e.user_id
WHERE e.created_at>=? AND e.created_at<?{exclusion} GROUP BY e.event_name""", base + excluded)}
            words = con.execute(f"""SELECT COALESCE(SUM(CAST(json_extract(e.metadata,'$.words_count') AS INTEGER)),0)
FROM analytics_events e JOIN users u ON u.telegram_id=e.user_id WHERE e.event_name='word_added' AND e.created_at>=? AND e.created_at<?{exclusion}""", base + excluded).fetchone()[0]
        return {"total_users": total, "new_users": new, "active_users": active, "events": counts, "words_added": int(words or 0)}

    def active_users(self, end_day: date, days: int) -> int:
        start = self._day_window(end_day - timedelta(days=days - 1)).start; end = self._day_window(end_day).end
        exclusion, excluded = self._excluded_sql("u.telegram_id"); marks = ",".join("?" for _ in MEANINGFUL_EVENTS)
        return self._scalar(f"""SELECT COUNT(DISTINCT e.user_id) FROM analytics_events e JOIN users u ON u.telegram_id=e.user_id
WHERE e.created_at>=? AND e.created_at<? AND e.event_name IN ({marks}){exclusion}""",
                            [iso_utc(start), iso_utc(end)] + sorted(MEANINGFUL_EVENTS) + excluded)

    def activation(self, cohort_day: date, as_of: datetime | None = None) -> dict:
        window = self._day_window(cohort_day); cutoff = min(window.end, (as_of or utc_now()).astimezone(timezone.utc))
        exclusion, excluded = self._excluded_sql("u.telegram_id")
        stages = ["folder_created", "word_added", "test_started", "test_completed"]
        with self.db.conn() as con:
            users = [r[0] for r in con.execute(f"SELECT telegram_id FROM users u WHERE datetime(created_at)>=datetime(?) AND datetime(created_at)<datetime(?){exclusion}",
                                               [iso_utc(window.start), iso_utc(window.end)] + excluded)]
            counts = {}
            for stage in stages:
                if not users: counts[stage] = 0; continue
                marks = ",".join("?" for _ in users)
                counts[stage] = con.execute(f"""SELECT COUNT(DISTINCT e.user_id) FROM analytics_events e JOIN users u ON u.telegram_id=e.user_id
WHERE e.event_name=? AND e.user_id IN ({marks}) AND e.created_at>=u.created_at AND e.created_at<datetime(u.created_at,'+24 hours') AND e.created_at<=?""",
                                            [stage] + users + [iso_utc(cutoff)]).fetchone()[0]
        return {"cohort": cohort_day.isoformat(), "registrations": len(users), "stages": counts,
                "preliminary": utc_now() < window.end + timedelta(hours=24)}

    def retention(self, as_of_day: date | None = None) -> dict:
        today = as_of_day or datetime.now(self.tz).date(); result = {}
        exclusion, excluded = self._excluded_sql("u.telegram_id"); events = sorted(MEANINGFUL_EVENTS); event_marks = ",".join("?" for _ in events)
        with self.db.conn() as con:
            for offset in (1, 7, 30):
                denominator = numerator = 0; cohorts = []
                oldest = con.execute(f"SELECT MIN(created_at) FROM users u WHERE 1=1{exclusion}", excluded).fetchone()[0]
                if not oldest:
                    result[f"d{offset}"] = {"returned": 0, "size": 0, "percent": None, "cohorts": []}; continue
                try: first_day = datetime.fromisoformat(oldest.replace(" ", "T")).replace(tzinfo=timezone.utc).astimezone(self.tz).date()
                except ValueError: first_day = today
                cohort_day = first_day
                while cohort_day <= today - timedelta(days=offset):
                    activity_day = cohort_day + timedelta(days=offset)
                    cwin = self._day_window(cohort_day); awin = self._day_window(activity_day)
                    users = [r[0] for r in con.execute(f"SELECT telegram_id FROM users u WHERE datetime(created_at)>=datetime(?) AND datetime(created_at)<datetime(?){exclusion}", [iso_utc(cwin.start), iso_utc(cwin.end)] + excluded)]
                    returned = 0
                    if users:
                        marks = ",".join("?" for _ in users)
                        returned = con.execute(f"SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE user_id IN ({marks}) AND event_name IN ({event_marks}) AND created_at>=? AND created_at<?",
                                               users + events + [iso_utc(awin.start), iso_utc(awin.end)]).fetchone()[0]
                    denominator += len(users); numerator += returned
                    cohorts.append({"date": cohort_day.isoformat(), "returned": returned, "size": len(users)})
                    cohort_day += timedelta(days=1)
                result[f"d{offset}"] = {"returned": numerator, "size": denominator, "percent": round(numerator * 100 / denominator) if denominator else None, "cohorts": cohorts}
        return result

    def sources(self) -> list[dict]:
        exclusion, excluded = self._excluded_sql("u.telegram_id")
        with self.db.conn() as con:
            rows = con.execute(f"SELECT COALESCE(NULLIF(ref_code,''),'organic') ref,COUNT(*) registrations FROM users u WHERE 1=1{exclusion} GROUP BY ref ORDER BY registrations DESC,ref", excluded).fetchall()
            result=[]
            for row in rows:
                ref=row["ref"]; users=[r[0] for r in con.execute(f"SELECT telegram_id FROM users u WHERE COALESCE(NULLIF(ref_code,''),'organic')=?{exclusion}",[ref]+excluded)]
                completed=0
                if users:
                    marks=','.join('?' for _ in users);completed=con.execute(f"SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE event_name='test_completed' AND user_id IN ({marks})",users).fetchone()[0]
                d1_size=d1_returned=0; today=datetime.now(self.tz).date()
                for user_id, created_at in con.execute(f"SELECT telegram_id,created_at FROM users u WHERE COALESCE(NULLIF(ref_code,''),'organic')=?{exclusion}",[ref]+excluded):
                    try: cohort=datetime.fromisoformat(created_at.replace(' ','T')).replace(tzinfo=timezone.utc).astimezone(self.tz).date()
                    except ValueError: continue
                    if cohort+timedelta(days=1)>today: continue
                    d1_size+=1;window=self._day_window(cohort+timedelta(days=1));marks=','.join('?' for _ in MEANINGFUL_EVENTS)
                    returned=con.execute(f"SELECT 1 FROM analytics_events WHERE user_id=? AND event_name IN ({marks}) AND created_at>=? AND created_at<? LIMIT 1",
                                         [user_id]+sorted(MEANINGFUL_EVENTS)+[iso_utc(window.start),iso_utc(window.end)]).fetchone()
                    d1_returned+=int(bool(returned))
                result.append({"source":ref,"registrations":row["registrations"],"activated":completed,
                               "activation_percent":round(completed*100/row["registrations"]) if row["registrations"] else 0,
                               "d1_returned":d1_returned,"d1_size":d1_size,
                               "d1_percent":round(d1_returned*100/d1_size) if d1_size else None})
            return result

    def snapshot(self, day: date | None = None, cutoff: time | None = None) -> dict:
        local_now=datetime.now(self.tz); day=day or local_now.date(); window=self._day_window(day,cutoff)
        values=self.period(window.start,window.end); values.update({"date":day.isoformat(),"dau":values["active_users"],"wau":self.active_users(day,7),"mau":self.active_users(day,30),"activation":self.activation(day),"retention":self.retention(day)})
        return values

    def product_metrics(self) -> dict:
        """Current product funnels for the private analytics bot."""
        exclusion, excluded = self._excluded_sql("u.telegram_id")
        with self.db.conn() as con:
            users=con.execute(f"SELECT COUNT(*) FROM users u WHERE 1=1{exclusion}",excluded).fetchone()[0]
            onboarding=con.execute(f"SELECT COUNT(*) FROM users u WHERE onboarding_completed_at IS NOT NULL{exclusion}",excluded).fetchone()[0]
            subscribed=con.execute(f"SELECT COUNT(*) FROM users u WHERE channel_subscribed=1{exclusion}",excluded).fetchone()[0]
            teachers=con.execute(f"SELECT COUNT(*) FROM users u WHERE usage_role='teacher'{exclusion}",excluded).fetchone()[0]
            classes=con.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
            joined=con.execute("SELECT COUNT(*) FROM class_members WHERE status='active'").fetchone()[0]
            assignments=con.execute("SELECT COUNT(*) FROM assignments WHERE active=1").fetchone()[0]
            completed=con.execute("SELECT COUNT(*) FROM assignment_recipients WHERE status='completed'").fetchone()[0]
            recipients=con.execute("SELECT COUNT(*) FROM assignment_recipients").fetchone()[0]
            referrals=con.execute(f"SELECT COUNT(*) FROM users u WHERE referrer_user_id IS NOT NULL{exclusion}",excluded).fetchone()[0]
        return {"users":users,"onboarding":onboarding,"subscribed":subscribed,"teachers":teachers,
                "classes":classes,"joined":joined,"assignments":assignments,"assignment_recipients":recipients,
                "assignment_completed":completed,"referrals":referrals}


def percent_change(current: int, previous: int) -> str:
    if previous == 0: return "—" if current == 0 else "+∞"
    return f"{(current-previous)*100/previous:+.0f}%"


def format_report(data: dict, title: str = "СЛОВО", preliminary: bool = False) -> str:
    events=data["events"]; active=data["dau"]; activation=data["activation"]; registrations=activation["registrations"]
    def stage(name):
        count=activation["stages"].get(name,0); pct=round(count*100/registrations) if registrations else 0
        return f"{count} / {registrations} — {pct}%"
    def retention(name):
        item=data["retention"][name]
        return "—" if item["percent"] is None else f'{item["percent"]}% — {item["returned"]}/{item["size"]}'
    per_words=data["words_added"]/active if active else 0; completed=events.get("test_completed",0); per_tests=completed/active if active else 0
    return "\n".join([
        f"📊 {title} — {data['date']}",
        "Предварительно, на момент отчёта" if preliminary else "Полный период",
        "", "👥 Пользователи", f"Всего: {data['total_users']}", f"Новых: {data['new_users']}", f"DAU: {data['dau']}", f"WAU: {data['wau']}", f"MAU: {data['mau']}",
        "", "🚀 Активация новых", f"Создали папку: {stage('folder_created')}", f"Добавили слово: {stage('word_added')}", f"Начали тест: {stage('test_started')}", f"Закончили тест: {stage('test_completed')}",
        "", "🔄 Retention", f"D1: {retention('d1')}", f"D7: {retention('d7')}", f"D30: {retention('d30')}",
        "", "📚 Engagement", f"Создано папок: {events.get('folder_created',0)}", f"Добавлено слов: {data['words_added']}", f"Начато тестов: {events.get('test_started',0)}", f"Завершено тестов: {completed}", f"Игр начато: {events.get('game_started',0)}", f"Игр завершено: {events.get('game_completed',0)}", f"Слов на активного: {per_words:.1f}", f"Тестов на активного: {per_tests:.1f}",
    ])
