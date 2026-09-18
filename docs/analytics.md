# Slovo: analytics and first-user launch

## Definitions

- All event timestamps are stored in UTC; calendar reporting uses `ANALYTICS_TIMEZONE` (`Europe/Moscow`).
- DAU is the number of distinct non-excluded users with meaningful activity on one calendar day.
- WAU and MAU are distinct users with meaningful activity during the last 7 and 30 calendar days, including the selected day.
- Meaningful events are `folder_created`, `word_added`, `test_started`, `test_completed`, `game_started`, `game_completed`, `shared_folder_copied`, and `assignment_completed`.
- Activation cohorts are registration dates in Moscow time. Each stage is reached only if its first matching event happened within 24 hours after registration.
- D1, D7 and D30 require meaningful activity on the exact cohort day + 1, + 7 or + 30. Only mature cohorts enter the denominator; combined percentages are weighted by user count.
- Test completion is server-created once per `test_session_id`, and enforces `known_count + unknown_count = words_count`.
- Acquisition is first-touch: the first valid code is stored once and later links never overwrite it. Missing or invalid codes become `organic`.

## Event catalogue

`app_open`, `registration_completed`, `folder_created`, `word_added`, `test_started`, `test_completed`, `game_started`, `game_completed`, `share_link_created`, `shared_folder_opened`, `shared_folder_copied`. Assignment event names are reserved but are not emitted because Slovo has no assignment entity yet.

Events contain only numeric/entity identifiers, counts, game type and aggregate results. Word text, messages, tokens and Telegram profile data are not written to analytics metadata.

## Environment

```dotenv
BOT_TOKEN=
BOT_USERNAME=LangSlovo_Bot
DATABASE_PATH=/opt/slovo/data/slovo.db
WEBAPP_URL=https://tennclub-slovo-timeweb-proxy-fb05.twc1.net/?release=20260918-6
WEBAPP_DEV_MODE=0
ANALYTICS_BOT_TOKEN=
ADMIN_TELEGRAM_ID=
ANALYTICS_EXCLUDED_TELEGRAM_IDS=123456789,987654321
ANALYTICS_TIMEZONE=Europe/Moscow
```

Never reuse `BOT_TOKEN` as `ANALYTICS_BOT_TOKEN`: two polling processes using one token conflict.

## Create the private Analytics Bot

1. Open `@BotFather`, send `/newbot`, choose a visible name such as `Slovo Analytics` and a unique username ending in `bot`.
2. Copy its token into `/opt/slovo/.env` as `ANALYTICS_BOT_TOKEN`. Do not paste it into chat, GitHub or logs.
3. Learn your numeric ID by messaging `@userinfobot` (or use Telegram Bot API `getUpdates` before starting polling) and put it in `ADMIN_TELEGRAM_ID`.
4. Put the same owner ID and all developer/test IDs into `ANALYTICS_EXCLUDED_TELEGRAM_IDS`.
5. Restart `slovo-analytics-bot.service`, then send `/help` and `/today` to the new bot. Other accounts receive no statistics.

Commands: `/today`, `/yesterday`, `/week`, `/retention`, `/sources`, `/help`.

## Backup and safe deployment

The install script creates a consistent SQLite backup automatically. Manual equivalent:

```bash
cd /opt/slovo
mkdir -p data/backups
.venv/bin/python - "data/backups/slovo-pre-analytics-$(date +%Y%m%d-%H%M%S).db" <<'PY'
import sqlite3, sys
source=sqlite3.connect('data/slovo.db'); target=sqlite3.connect(sys.argv[1])
source.backup(target); target.close(); source.close()
PY
```

Apply the additive migration and install services:

```bash
cd /opt/slovo
bash deploy/timeweb/install.sh
systemctl is-active slovo-bot slovo-web slovo-analytics-bot
systemctl list-timers slovo-analytics-report.timer --no-pager
```

The migration is applied idempotently when the Python application starts. It creates `analytics_events`, `analytics_report_deliveries`, `schema_migrations`, indexes, and adds `usage_role`, `acquisition_source`, `campaign`, `ref_code`, `acquired_at` to users.

Manual report test (does not affect analytics metrics):

```bash
cd /opt/slovo
.venv/bin/python analytics_bot.py --daily-report --force
```

The timer runs at 21:00 `Europe/Moscow`, survives reboot (`Persistent=true`), and sends a same-day snapshot. Automatic retries do not resend an already recorded date.

## Rollback

Code rollback is safe because all schema changes are additive:

1. stop the three changed services;
2. restore the pre-deploy application archive;
3. restore the backup database only if the migration itself failed or data integrity is not `ok`;
4. restart the old bot/web services.

```bash
systemctl stop slovo-analytics-bot slovo-analytics-report.timer slovo-bot slovo-web
cp /opt/slovo/data/backups/CHOSEN_BACKUP.db /opt/slovo/data/slovo.db
sqlite3 /opt/slovo/data/slovo.db 'PRAGMA integrity_check;'
systemctl start slovo-bot slovo-web
```

Do not delete analytics tables during rollback: older code ignores them. Restoring a backup discards user actions made after that backup, so use it only for an actual migration/data failure.

## Referral links

```bash
.venv/bin/python scripts/generate_referral.py tutor_001
.venv/bin/python scripts/generate_referral.py school_001 --app YOUR_APP_SHORT_NAME
```

Examples:

- `https://t.me/LangSlovo_Bot?start=tutor_001`
- `https://t.me/LangSlovo_Bot?start=school_001`
- `https://t.me/LangSlovo_Bot/YOUR_APP_SHORT_NAME?startapp=tutor_001`

Invitation tokens beginning with `inv_` remain folder invitations and are not acquisition codes.

## Owner pre-launch checklist

- [ ] Create a separate Analytics Bot through BotFather.
- [ ] Add `ANALYTICS_BOT_TOKEN` and `ADMIN_TELEGRAM_ID` to Timeweb `.env`.
- [ ] Add owner/developer/test IDs to `ANALYTICS_EXCLUDED_TELEGRAM_IDS`.
- [ ] Make and verify a production database backup.
- [ ] Deploy files and run `deploy/timeweb/install.sh`.
- [ ] Check all three services and the timer logs.
- [ ] Run `/today`, `/yesterday`, `/retention`, `/sources`.
- [ ] Force one manual daily report.
- [ ] Create and open a test referral as a genuinely new user.
- [ ] Create a folder, add 10 words, complete a test, and verify exactly one `test_completed`.
- [ ] Verify the test account is absent from reports.
- [ ] Only then distribute separate links to the first teachers and schools.

