"""保存済み Cookie を使って申込フローの構造を調査する。

⚠️ 安全装置:
  - 「申込」「確定」「OK」を含むボタンには絶対に触らない
  - 各ステップでフォーム構造を保存して終了
  - 進む際は「次へ」「進む」相当のナビボタンのみクリック
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
DOCS_DIR.mkdir(exist_ok=True)

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
STORAGE_STATE = DATA_DIR / "storage_state.json"

# 「絶対に押してはいけない」ボタンの判定キーワード
DANGER_KEYWORDS = ["申込み", "申込", "確定", "OK", "送信", "submit"]
SAFE_NAV_KEYWORDS = ["次へ", "進む", "確認", "資格"]


def is_dangerous_label(text: str) -> bool:
    return any(k in text for k in DANGER_KEYWORDS)


def capture_page(page: Page, label: str) -> dict:
    """現在ページの構造を JSON で取得。"""
    info = {
        "label": label,
        "url": page.url,
        "title": page.title(),
    }
    try:
        info["forms"] = page.evaluate(
            """() => Array.from(document.querySelectorAll('form')).map(f => ({
                name: f.name, action: f.action, method: f.method,
                inputs: Array.from(f.querySelectorAll('input,select,textarea,button'))
                    .map(el => ({
                        tag: el.tagName, type: el.type || null,
                        name: el.name || null, id: el.id || null,
                        value: (el.value && el.value.length < 80) ? el.value : null
                    }))
            }))"""
        )
    except Exception as e:
        info["forms_error"] = str(e)
    try:
        info["nav_elements"] = page.evaluate(
            """() => Array.from(document.querySelectorAll('a, img, input[type=image], input[type=button], input[type=submit], button'))
                .map(el => ({
                    tag: el.tagName,
                    type: el.type || null,
                    text: (el.textContent || el.value || el.alt || '').trim().slice(0, 40),
                    alt: el.alt || null,
                    onclick: (el.getAttribute('onclick') || '').slice(0, 200),
                    href: el.href || null
                }))
                .filter(x => x.text || x.onclick || x.alt)"""
        )
    except Exception:
        pass
    return info


def main() -> None:
    if not STORAGE_STATE.exists():
        print(f"ERROR: {STORAGE_STATE} がありません。jkk_login.py を先に実行してください。")
        return

    out: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 保存済み Cookie を読み込み
        ctx = browser.new_context(
            locale="ja-JP",
            storage_state=str(STORAGE_STATE),
        )
        page = ctx.new_page()
        with ctx.expect_page() as popup_info:
            page.goto(START_URL, wait_until="load")
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # 検索 → cz9 → 詳細 → 申込（ログイン画面はスキップされて申込み確認に直接行くはず）
        with popup.expect_navigation():
            popup.click('a:has-text("検索")')
        popup.wait_for_load_state("domcontentloaded")
        with popup.expect_navigation():
            popup.evaluate("submitPage('9')")
        popup.wait_for_load_state("domcontentloaded")
        with popup.expect_navigation():
            popup.click('img[alt="詳細"]', force=True)
        popup.wait_for_load_state("domcontentloaded")
        with popup.expect_navigation():
            popup.click('img[alt*="申込"]', force=True)
        popup.wait_for_load_state("domcontentloaded")
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # ★ ここが「申込み確認」ページ（あるいはログインへ）
        out.append(capture_page(popup, "申込ボタン直後（ログイン状態なら申込み確認）"))

        # 安全に進めるかチェック: title に「ログイン」が入っていれば未認証 → 中断
        if "ログイン" in popup.title():
            out[-1]["danger"] = "未ログインのため中断"
            print("⚠ ログイン画面に到達。Cookie切れの可能性あり。")
        else:
            # 「次へ」「進む」「資格」など SAFE なナビボタンを探す
            nav = popup.evaluate(
                """() => Array.from(document.querySelectorAll('a, img'))
                    .map(el => ({
                        text: (el.textContent || el.alt || '').trim(),
                        alt: el.alt || '',
                        onclick: (el.getAttribute('onclick') || '').slice(0, 200)
                    }))
                    .filter(x => x.text || x.alt)"""
            )
            out[-1]["available_nav_options"] = nav

        # ここでは申込フォームには進まない。
        # 構造を見て、次のステップを決める。
        browser.close()

    out_path = DOCS_DIR / "apply_flow_step1.md"
    lines: list[str] = [f"# 申込フロー調査 step1 ({datetime.now().isoformat()})\n"]
    for entry in out:
        lines.append(f"## {entry['label']}")
        lines.append(f"- URL: `{entry.get('url')}`")
        lines.append(f"- title: `{entry.get('title')}`")
        if "danger" in entry:
            lines.append(f"- ⚠ {entry['danger']}")
        for key in ("forms", "available_nav_options", "nav_elements"):
            if key in entry:
                lines.append(f"- {key}:")
                lines.append("```json")
                lines.append(
                    json.dumps(entry[key], indent=2, ensure_ascii=False)[:8000]
                )
                lines.append("```")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK: {out_path}")


if __name__ == "__main__":
    main()
