"""JKKねっとにログインしてセッション Cookie を data/storage_state.json に保存する。

JKK の仕様上、直接アクセスできるログインURLが存在せず、物件詳細→申込ボタン経由でしか
ログイン画面に辿り着けない。そのため当ツールは:
  1. 検索画面 → 適当なクラスタを開いて物件詳細 → 申込ボタンを押す
  2. 表示されたログインフォームに .env の認証情報を入力
  3. ログイン送信
  4. 成功判定→ storage_state.json 保存（失敗時はエラー終了）
  5. ※ 申込フォームには進まずに即終了（誤申込防止）

使い方:
    .venv/Scripts/python.exe tools/jkk_login.py
    .venv/Scripts/python.exe tools/jkk_login.py --headed   # 可視ブラウザで動作確認
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
STORAGE_STATE = DATA_DIR / "storage_state.json"

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
# 物件のあるクラスタ（cz6,9,10,11 など）。空の場合はループする
PROBE_CZ_NUMBERS = [9, 10, 11, 6, 1, 2, 3, 4, 5, 7, 8]


def _navigate_to_login_form(popup: Page) -> bool:
    """popup を ログインフォームまで遷移させる。成功時 True。"""
    # 検索ボタン → 地図
    with popup.expect_navigation(timeout=20000):
        popup.click('a:has-text("検索")')
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # 物件のあるクラスタを順に試す
    for cz_no in PROBE_CZ_NUMBERS:
        try:
            with popup.expect_navigation(timeout=15000):
                popup.evaluate(f"submitPage('{cz_no}')")
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
            try:
                popup.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        except Exception:
            continue

        # 詳細ボタンが存在するか
        if popup.locator('img[alt="詳細"]').count() == 0:
            # 戻るのは ERR_CACHE_MISS する可能性があるため
            # この関数では簡易: ロケータ無ければ次の czNo へ進めない
            # → fresh popup する別フロー必要だが、最初に当たれば良いので
            #   ここではそのままbreakし、外側で再試行
            return False
        break
    else:
        return False

    # 1件目の詳細をクリック
    with popup.expect_navigation(timeout=15000):
        popup.click('img[alt="詳細"]', force=True)
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # 申込ボタン（img alt=申込）→ ログインフォーム
    with popup.expect_navigation(timeout=15000):
        popup.click('img[alt*="申込"]', force=True)
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # password input が見えていれば成功
    return popup.locator('input[type="password"]').count() > 0


def _do_login(popup: Page, user_id: str, password: str) -> tuple[bool, str]:
    popup.fill('input[name="loginRM.loginM.userId"]', user_id)
    popup.fill('input[name="loginRM.loginM.password"]', password)

    # JKKのログインボタンは <a><img alt="ログイン"></a> 構造で
    # onclick="loginPage('<csrf-nonce>')" → submitAction('login') で送信される
    clicked = False
    try:
        with popup.expect_navigation(timeout=20000):
            popup.click('a:has(img[alt="ログイン"])', force=True)
        clicked = True
    except Exception:
        pass

    if not clicked:
        # fallback: <a> の onclick から CSRF nonce を抽出して loginPage を直接呼ぶ
        try:
            kbn = popup.evaluate(
                """() => {
                    const a = Array.from(document.querySelectorAll('a'))
                        .find(x => x.querySelector('img[alt=\"ログイン\"]'));
                    if (!a) return null;
                    const m = (a.getAttribute('onclick') || '')
                        .match(/loginPage\\('([^']+)'\\)/);
                    return m ? m[1] : null;
                }"""
            )
            if kbn:
                with popup.expect_navigation(timeout=20000):
                    popup.evaluate(f"loginPage('{kbn}')")
                clicked = True
        except Exception:
            pass

    if not clicked:
        return False, "送信ボタンが見つかりませんでした"

    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    title = popup.title()
    url = popup.url
    # ログインに成功すると title から「ログイン」が消えるはず
    # 失敗時は同じ画面 or エラー画面
    if "ログイン" in title:
        # まだログイン画面、エラー文言を取得
        body_text = popup.evaluate(
            "() => document.body.innerText.slice(0, 500)"
        )
        return False, f"ログイン失敗: title={title} url={url} body={body_text!r}"

    return True, f"login OK: title={title!r}, url={url}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--headed", action="store_true", help="ブラウザ可視モード（デバッグ用）"
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    user_id = os.environ.get("JKK_USER_ID", "").strip()
    password = os.environ.get("JKK_PASSWORD", "")
    if not user_id or not password:
        print(
            "ERROR: JKK_USER_ID / JKK_PASSWORD が .env に未設定です。"
            "tools/set_jkk_credentials.py で設定してください。"
        )
        return 1

    print(f"[*] login as {user_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()
        with ctx.expect_page() as popup_info:
            page.goto(START_URL, wait_until="load")
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        if not _navigate_to_login_form(popup):
            print("ERROR: ログインフォームに到達できませんでした（物件0件の時間帯か）")
            browser.close()
            return 2

        ok, msg = _do_login(popup, user_id, password)
        print(msg)
        if not ok:
            browser.close()
            return 3

        # 申込フォームには進まず、ログイン直後の Cookie を保存
        ctx.storage_state(path=str(STORAGE_STATE))
        print(f"[OK] Cookie保存: {STORAGE_STATE}")

        browser.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
