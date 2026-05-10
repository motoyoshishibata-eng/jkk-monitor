"""通知を受け取った物件の申込資格確認ページまで一気に遷移する。

使い方:
    python -m src.quick_apply <jkk_id> [--cz <番号>]

例:
    python -m src.quick_apply 5080040_0003
    python -m src.quick_apply 5080040_0003 --cz 9

仕組み:
    1. data/storage_state.json の保存済み Cookie でログイン状態を再現
    2. 検索画面 → 各クラスタを巡回 → jkk_id 一致の「詳細」クリック
    3. 物件詳細 → 「申込」 → 申込資格確認 ページに到達
    4. ブラウザを開いたまま停止。Enterで終了

⚠️ このスクリプトは「最後の申込ボタン」を絶対に押しません。
   優先募集の選択以降はユーザが手動で操作してください。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).parent.parent
STORAGE_STATE = ROOT / "data" / "storage_state.json"
START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
DEFAULT_CZ_ORDER = [9, 10, 11, 6, 1, 2, 3, 4, 5, 7, 8]


def _wait_quiet(popup: Page) -> None:
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass


def _open_to_map(p, popup_existing: Page | None = None):
    """既存セッションでpopup→検索→地図ページに到達。新ブラウザを返す。"""
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context(
        locale="ja-JP",
        storage_state=str(STORAGE_STATE) if STORAGE_STATE.exists() else None,
    )
    page = ctx.new_page()
    with ctx.expect_page(timeout=15000) as popup_info:
        page.goto(START_URL, wait_until="load", timeout=30000)
    popup = popup_info.value
    _wait_quiet(popup)
    with popup.expect_navigation(timeout=20000):
        popup.click('a:has-text("検索")')
    _wait_quiet(popup)
    return browser, ctx, popup


def _try_cluster(popup: Page, cz_no: int, jyutaku_cd: str, yusen_kbn: str) -> bool:
    """クラスタ内に該当物件があれば 詳細ページまで遷移して True。"""
    try:
        with popup.expect_navigation(timeout=15000):
            popup.evaluate(f"submitPage('{cz_no}')")
        _wait_quiet(popup)
    except Exception:
        return False

    # senPage('', mskKbn, jyutakuCd, yusenKbn) の jyutakuCd, yusenKbn が一致するリンクを探す
    found = popup.evaluate(
        f"""() => {{
            const links = Array.from(document.querySelectorAll('a[onclick*="senPage"]'));
            for (const a of links) {{
                const oc = a.getAttribute('onclick') || '';
                const m = oc.match(
                    /senPage\\('[^']*','[^']*','([^']+)','([^']+)'\\)/
                );
                if (m && m[1] === '{jyutaku_cd}' && m[2] === '{yusen_kbn}') {{
                    a.click();
                    return true;
                }}
            }}
            return false;
        }}"""
    )
    if not found:
        return False

    # 詳細ページの読み込みを待つ
    _wait_quiet(popup)
    return True


def _go_to_apply_check(popup: Page) -> bool:
    """物件詳細ページから「申込」ボタンを押して申込資格確認画面まで進む。"""
    try:
        with popup.expect_navigation(timeout=15000):
            popup.click('img[alt*="申込"]', force=True)
        _wait_quiet(popup)
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "jkk_id",
        help="物件の jkk_id (例: 5080040_0003)。Discord通知に含まれる",
    )
    parser.add_argument(
        "--cz",
        type=int,
        default=None,
        help="クラスタ番号(1-11)。未指定なら全11クラスタを順に試行",
    )
    args = parser.parse_args()

    if not STORAGE_STATE.exists():
        print(
            f"ERROR: {STORAGE_STATE} がありません。"
            "tools/jkk_login.py を先に実行してください。"
        )
        return 1

    if "_" not in args.jkk_id:
        print(f"ERROR: jkk_id 形式不正: {args.jkk_id} (例: 5080040_0003)")
        return 1
    jyutaku_cd, yusen_kbn = args.jkk_id.split("_", 1)
    cz_order = [args.cz] if args.cz else DEFAULT_CZ_ORDER

    print(f"[*] 物件 {args.jkk_id} (jyutakuCd={jyutaku_cd}, yusenKbn={yusen_kbn})")
    print(f"[*] 試行クラスタ: {cz_order}")

    with sync_playwright() as p:
        for cz_no in cz_order:
            browser, ctx, popup = _open_to_map(p)
            try:
                print(f"[..] cz{cz_no} を試行中")
                if not _try_cluster(popup, cz_no, jyutaku_cd, yusen_kbn):
                    browser.close()
                    continue

                if not _go_to_apply_check(popup):
                    print(f"[!] cz{cz_no}: 詳細→申込 で停止")
                    browser.close()
                    continue

                title = popup.title()
                url = popup.url
                if "ログイン" in title:
                    print(
                        f"[!] ログイン画面に戻った（Cookie切れ）。"
                        f"tools/jkk_login.py を再実行してください。"
                    )
                    browser.close()
                    return 2

                print()
                print("=" * 60)
                print(f"[OK] {title}")
                print(f"     {url}")
                print("=" * 60)
                print()
                print("ブラウザに申込資格確認画面が表示されています。")
                print("以降は手動で操作してください:")
                print("  1. 優先募集の選択")
                print("  2.「確認のうえ申込」ボタンをクリック")
                print("  3. 世帯情報・収入を入力")
                print("  4. 最終「申込」ボタンで確定")
                print()
                input("ブラウザを閉じてこのプログラムを終了するには Enter: ")
                browser.close()
                return 0
            except Exception as e:
                print(f"[!] cz{cz_no}: 例外発生 {e}")
                browser.close()
                continue

    print(
        "[ERROR] 該当物件が全クラスタで見つかりませんでした。"
        "jkk_id を確認するか、物件が取下げ済みの可能性があります。"
    )
    return 3


if __name__ == "__main__":
    sys.exit(main())
