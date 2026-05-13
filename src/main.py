"""JKK監視ジョブのメインエントリポイント。

cron / GitHub Actions から呼ばれる想定:
    python -m src.main

config.yaml から条件を読込み、JKKを検索し、新規物件があれば通知し、stateを保存する。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .diff import detect, update_state
from .fetcher import fetch_listings
from .notifier.base import Notifier
from .notifier.discord import DiscordNotifier
from .storage import load_state, save_state

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"
STATE_PATH = ROOT / "data" / "state.json"

# fetch失敗が何回連続したらメール通知（=ワークフローを失敗終了）するか。
# 5分間隔の実行なので 5 = 約25分の取得不能を許容。
FAILURE_NOTIFY_THRESHOLD = 5

logger = logging.getLogger("jkk_monitor")


def _setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )
    # httpx は Discord Webhook URL をリクエストログに出してしまうため抑制
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_notifiers(config: dict) -> list[Notifier]:
    notifiers: list[Notifier] = []
    notif_cfg = config.get("notifications", {})

    if notif_cfg.get("discord", {}).get("enabled"):
        url = os.environ.get("DISCORD_WEBHOOK_URL")
        if url:
            notifiers.append(DiscordNotifier(url))
        else:
            logger.warning(
                "discord.enabled だが DISCORD_WEBHOOK_URL 未設定 → 通知スキップ"
            )

    # LINE / Telegram は今後の実装（Phase 1 では Discord のみ）
    return notifiers


def run() -> int:
    """1回分の監視を実行する。

    終了コード:
        0 = 成功 / fetch失敗だが連続失敗しきい値未満（メール抑制）
        1 = 連続失敗がしきい値到達（GitHubが失敗メールを送る）
        2 = 設定エラー
    """
    _setup_logging()
    load_dotenv(ROOT / ".env")

    try:
        config = _load_config()
    except Exception as e:
        logger.error("config.yaml 読込失敗: %s", e)
        return 2

    filters = config.get("filters", {}) or {}
    areas = filters.get("areas") or []
    if not areas:
        logger.error("filters.areas が空。config.yaml に監視対象を設定してください。")
        return 2

    rent_max = filters.get("rent_max")
    logger.info("検索条件: areas=%s, rent_max=%s", areas, rent_max)

    state = load_state(STATE_PATH)

    try:
        current = fetch_listings(areas=areas, rent_max=rent_max)
    except Exception as e:
        logger.exception("fetch失敗: %s", e)
        state.consecutive_failures += 1
        logger.warning(
            "連続失敗カウンタ: %d / %d",
            state.consecutive_failures,
            FAILURE_NOTIFY_THRESHOLD,
        )
        # しきい値に到達した瞬間だけ exit 1（GitHub が失敗メールを送る）。
        # それ以前および既通知後は exit 0 で抑制する。
        if (
            state.consecutive_failures >= FAILURE_NOTIFY_THRESHOLD
            and not state.failure_notified
        ):
            state.failure_notified = True
            save_state(state, STATE_PATH)
            logger.error(
                "連続%d回失敗。メール通知のため exit 1 します。",
                state.consecutive_failures,
            )
            return 1
        save_state(state, STATE_PATH)
        return 0

    logger.info("取得件数: %d", len(current))

    if state.consecutive_failures or state.failure_notified:
        logger.info(
            "fetch復旧。連続失敗カウンタ %d → 0、failure_notified リセット。",
            state.consecutive_failures,
        )
        state.consecutive_failures = 0
        state.failure_notified = False

    diff_result = detect(current, state)
    logger.info(
        "差分: 新規=%d / 既存=%d / 消失=%d",
        len(diff_result.new),
        len(diff_result.unchanged),
        len(diff_result.disappeared),
    )

    notifiers = _build_notifiers(config)
    if diff_result.new and not notifiers:
        logger.warning(
            "新規物件があるが、有効な通知先がない（DISCORD_WEBHOOK_URL未設定？）"
        )

    for listing in diff_result.new:
        logger.info(
            "新規: %s %s rent=%d", listing.name, listing.room, listing.rent
        )
        for notifier in notifiers:
            try:
                notifier.send(listing)
            except Exception as e:
                logger.exception("通知失敗 (%s): %s", type(notifier).__name__, e)

    new_state = update_state(state, current)
    save_state(new_state, STATE_PATH)
    logger.info("state保存: %s (%d件)", STATE_PATH, len(new_state.known_listings))

    return 0


if __name__ == "__main__":
    sys.exit(run())
