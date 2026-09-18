"""Private owner-only Analytics Bot and scheduled Slovo report sender."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, time, timedelta

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from dotenv import load_dotenv

from analytics import Analytics, format_report, iso_utc, percent_change
from slovo import DB

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
router = Router()


def settings() -> tuple[str, int]:
    token = os.getenv("ANALYTICS_BOT_TOKEN", "").strip()
    try: admin_id = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
    except ValueError: admin_id = 0
    if not token or not admin_id:
        raise RuntimeError("Set ANALYTICS_BOT_TOKEN and ADMIN_TELEGRAM_ID")
    return token, admin_id


def services() -> tuple[DB, Analytics]:
    database = DB(os.getenv("DATABASE_PATH", "data/slovo.db"))
    return database, Analytics(database)


def authorized(message: Message) -> bool:
    try: return message.from_user is not None and message.from_user.id == settings()[1]
    except RuntimeError: return False


async def deny(message: Message) -> bool:
    if authorized(message): return False
    await message.answer("Команда недоступна.")
    return True


def today_report(analytics: Analytics) -> str:
    now = datetime.now(analytics.tz)
    current=analytics.snapshot(now.date(),now.time());previous=analytics.snapshot(now.date()-timedelta(days=1),now.time())
    text=format_report(current,preliminary=True)
    return text+"\n\n📊 Изменение к предыдущему дню (одинаковый cutoff)\n"+"\n".join([
        f"DAU: {percent_change(current['dau'],previous['dau'])}",
        f"Новые пользователи: {percent_change(current['new_users'],previous['new_users'])}",
        f"Завершённые тесты: {percent_change(current['events'].get('test_completed',0),previous['events'].get('test_completed',0))}",
    ])


def yesterday_report(analytics: Analytics) -> str:
    day = datetime.now(analytics.tz).date() - timedelta(days=1)
    return format_report(analytics.snapshot(day), preliminary=False)


def week_report(analytics: Analytics) -> str:
    now = datetime.now(analytics.tz); start_day = now.date() - timedelta(days=6)
    values = analytics.period(analytics._day_window(start_day).start, analytics._day_window(now.date()).end)
    events = values["events"]; active = values["active_users"]
    return "\n".join([
        f"📊 СЛОВО — последние 7 дней ({start_day} — {now.date()})", "",
        f"Новых пользователей: {values['new_users']}", f"Активных пользователей: {active}",
        f"Создано папок: {events.get('folder_created',0)}", f"Добавлено слов: {values['words_added']}",
        f"Начато тестов: {events.get('test_started',0)}", f"Завершено тестов: {events.get('test_completed',0)}",
        f"Игр начато: {events.get('game_started',0)}", f"Игр завершено: {events.get('game_completed',0)}",
        f"Тестов на активного: {events.get('test_completed',0)/active:.1f}" if active else "Тестов на активного: 0.0",
    ])


def retention_report(analytics: Analytics) -> str:
    data = analytics.retention(); lines = ["🔄 Retention Slovo"]
    for key in ("d1", "d7", "d30"):
        item=data[key]; value="—" if item["percent"] is None else f'{item["percent"]}% — {item["returned"]}/{item["size"]}'
        lines.append(f"{key.upper()}: {value}")
    lines.append("")
    for row in data["d1"]["cohorts"][-10:]: lines.append(f"{row['date']}: {row['returned']}/{row['size']}")
    return "\n".join(lines)


def sources_report(analytics: Analytics) -> str:
    rows=analytics.sources()
    if not rows: return "📣 Источники\nДанных пока нет."
    lines=["📣 Источники пользователей"]
    for row in rows:
        lines += ["", row["source"], f"Регистраций: {row['registrations']}",
                  f"Завершили первый тест: {row['activated']}", f"Activation: {row['activation_percent']}%",
                  "D1: —" if row["d1_percent"] is None else f"D1: {row['d1_returned']}/{row['d1_size']} — {row['d1_percent']}%"]
    return "\n".join(lines)


@router.message(Command("today"))
async def cmd_today(message: Message):
    if await deny(message): return
    _, analytics=services(); await message.answer(today_report(analytics))


@router.message(Command("yesterday"))
async def cmd_yesterday(message: Message):
    if await deny(message): return
    _, analytics=services(); await message.answer(yesterday_report(analytics))


@router.message(Command("week"))
async def cmd_week(message: Message):
    if await deny(message): return
    _, analytics=services(); await message.answer(week_report(analytics))


@router.message(Command("retention"))
async def cmd_retention(message: Message):
    if await deny(message): return
    _, analytics=services(); await message.answer(retention_report(analytics))


@router.message(Command("sources"))
async def cmd_sources(message: Message):
    if await deny(message): return
    _, analytics=services(); await message.answer(sources_report(analytics))


@router.message(Command("help", "start"))
async def cmd_help(message: Message):
    if await deny(message): return
    await message.answer("Команды Slovo Analytics:\n/today\n/yesterday\n/week\n/retention\n/sources\n/help")


async def send_daily(force: bool = False) -> bool:
    token, admin_id=settings(); database, analytics=services(); now=datetime.now(analytics.tz); key=now.date().isoformat()
    with database.conn() as con:
        if not force and con.execute("SELECT 1 FROM analytics_report_deliveries WHERE report_date=?",(key,)).fetchone(): return False
    async with Bot(token) as bot: await bot.send_message(admin_id, today_report(analytics))
    with database.conn() as con:
        con.execute("INSERT OR REPLACE INTO analytics_report_deliveries(report_date,delivered_at,status) VALUES(?,?,'sent')",(key,iso_utc()))
    return True


async def run_polling():
    token,_=settings(); dispatcher=Dispatcher(); dispatcher.include_router(router)
    async with Bot(token) as bot: await dispatcher.start_polling(bot, allowed_updates=["message"])


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--daily-report",action="store_true");parser.add_argument("--force",action="store_true");args=parser.parse_args()
    asyncio.run(send_daily(args.force) if args.daily_report else run_polling())


if __name__ == "__main__": main()
