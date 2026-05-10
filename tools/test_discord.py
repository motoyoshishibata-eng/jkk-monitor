"""Discord Webhook の動作確認用。

使い方:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \
    python tools/test_discord.py

または .env に DISCORD_WEBHOOK_URL を入れて:
    python tools/test_discord.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.models import Listing
from src.notifier.discord import DiscordNotifier

load_dotenv(ROOT / ".env")

webhook = os.environ.get("DISCORD_WEBHOOK_URL")
if not webhook:
    print("DISCORD_WEBHOOK_URL が設定されていません")
    sys.exit(1)

sample = Listing(
    name="動作確認用ダミーハイツ",
    room="201",
    rent=85000,
    address="小金井市本町1-2-3",
    layout="2DK",
    area_m2=45.0,
    url="https://jhomes.to-kousya.or.jp/",
)

DiscordNotifier(webhook).send(sample)
print("[OK] 通知送信成功。Discord アプリで受信を確認してください。")
