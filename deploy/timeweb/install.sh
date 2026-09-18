#!/usr/bin/env bash
set -euo pipefail

cd /opt/slovo
mkdir -p data/backups data/avatars

if [[ -f data/slovo.db ]]; then
  BACKUP_PATH="data/backups/slovo-before-miniapp-$(date +%Y%m%d-%H%M%S).db"
  .venv/bin/python - "$BACKUP_PATH" <<'PY'
import sqlite3
import sys
source = sqlite3.connect("data/slovo.db")
target = sqlite3.connect(sys.argv[1])
with target:
    source.backup(target)
target.close()
source.close()
PY
  echo "Database backup: /opt/slovo/$BACKUP_PATH"
fi

.venv/bin/pip install -r requirements.txt
.venv/bin/python catalog_data.py
install -m 0644 deploy/timeweb/slovo-bot.service /etc/systemd/system/slovo-bot.service
install -m 0644 deploy/timeweb/slovo-web.service /etc/systemd/system/slovo-web.service
install -m 0644 deploy/timeweb/slovo-analytics-bot.service /etc/systemd/system/slovo-analytics-bot.service
install -m 0644 deploy/timeweb/slovo-analytics-report.service /etc/systemd/system/slovo-analytics-report.service
install -m 0644 deploy/timeweb/slovo-analytics-report.timer /etc/systemd/system/slovo-analytics-report.timer

# The old service name was used by the first Slovo release.
systemctl disable --now slovo.service 2>/dev/null || true
systemctl daemon-reload
systemctl enable slovo-bot.service slovo-web.service
systemctl restart slovo-bot.service slovo-web.service
if grep -q '^ANALYTICS_BOT_TOKEN=.' .env && grep -q '^ADMIN_TELEGRAM_ID=.' .env; then
  systemctl enable slovo-analytics-bot.service slovo-analytics-report.timer
  systemctl restart slovo-analytics-bot.service
  systemctl restart slovo-analytics-report.timer
else
  echo "Analytics Bot not started: add ANALYTICS_BOT_TOKEN and ADMIN_TELEGRAM_ID to .env"
  systemctl disable --now slovo-analytics-bot.service slovo-analytics-report.timer 2>/dev/null || true
fi

systemctl --no-pager --full status slovo-bot.service slovo-web.service
systemctl --no-pager --full status slovo-analytics-bot.service slovo-analytics-report.timer 2>/dev/null || true
