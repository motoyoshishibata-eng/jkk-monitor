"""Discord Webhook URL を .env に設定する対話ツール。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def main() -> int:
    if not ENV.exists():
        if ENV_EXAMPLE.exists():
            ENV.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV.write_text("DISCORD_WEBHOOK_URL=\n", encoding="utf-8")

    print("=" * 60)
    print("Discord Webhook URL 設定")
    print("=" * 60)
    print()
    print("Discord でコピーした Webhook URL を下の行に貼り付けて Enter:")
    print("（PowerShell では右クリックで貼り付け、cmd では Ctrl+V）")
    print()
    url = input("URL: ").strip()

    if not url:
        print("URL が空です。中断します。")
        return 1
    if not url.startswith("https://discord.com/api/webhooks/") and not url.startswith(
        "https://discordapp.com/api/webhooks/"
    ):
        print(f"[警告] discord.com の Webhook URL に見えません: {url[:50]}...")
        confirm = input("このまま保存しますか？ (y/N): ").strip().lower()
        if confirm != "y":
            print("中断しました。")
            return 1

    lines = ENV.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines: list[str] = []
    for line in lines:
        if line.startswith("DISCORD_WEBHOOK_URL="):
            new_lines.append(f"DISCORD_WEBHOOK_URL={url}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"DISCORD_WEBHOOK_URL={url}")

    ENV.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print()
    print(f"[OK] {ENV} に保存しました（先頭40文字: {url[:40]}...）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
