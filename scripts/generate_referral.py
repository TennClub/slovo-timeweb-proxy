"""Print valid first-touch Slovo referral links without storing any secret."""
import argparse
import os

from dotenv import load_dotenv

from analytics import normalize_ref

load_dotenv()
parser = argparse.ArgumentParser()
parser.add_argument("code", help="For example tutor_001 or school_001")
parser.add_argument("--bot", default=os.getenv("BOT_USERNAME", ""))
parser.add_argument("--app", default=os.getenv("APP_SHORT_NAME", ""))
args = parser.parse_args()
code = normalize_ref(args.code)
if code == "organic" and args.code != "organic": raise SystemExit("Use 1-64 letters, digits, '_' or '-'")
bot = args.bot.lstrip("@")
if not bot: raise SystemExit("Set BOT_USERNAME or pass --bot")
print(f"Bot: https://t.me/{bot}?start={code}")
if args.app: print(f"Mini App: https://t.me/{bot}/{args.app}?startapp={code}")

