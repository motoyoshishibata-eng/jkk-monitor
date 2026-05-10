"""ログインフロー調査 第2段: 物件詳細 → 申込ボタン経由でログイン画面に到達。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"


def main() -> None:
    out: list[str] = []
    out.append(f"# ログイン調査 第2段 ({datetime.now().isoformat()})\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()

        # popup で検索画面に到達
        with ctx.expect_page() as popup_info:
            page.goto(START_URL, wait_until="load")
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded")
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        # 検索 → 地図 → cz9 へ（東大和市等の物件あり）
        with popup.expect_navigation():
            popup.click('a:has-text("検索")')
        popup.wait_for_load_state("domcontentloaded")
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        with popup.expect_navigation():
            popup.evaluate("submitPage('9')")
        popup.wait_for_load_state("domcontentloaded")
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        out.append(f"## cz9 (物件あり) URL: `{popup.url}`")
        out.append(f"- title: `{popup.title()}`\n")

        # 「詳細」ボタンを最初の1件でクリック
        out.append("## 詳細ボタンクリック")
        try:
            with popup.expect_navigation(timeout=15000):
                popup.click('img[alt="詳細"]', force=True)
            popup.wait_for_load_state("domcontentloaded")
            try:
                popup.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            out.append(f"- URL: `{popup.url}`")
            out.append(f"- title: `{popup.title()}`\n")
        except Exception as e:
            out.append(f"- 詳細クリック失敗: {e}\n")
            out.append(f"- 現在URL: `{popup.url}`\n")

        # 詳細ページのリンク・ボタンを列挙
        try:
            buttons = popup.evaluate(
                """() => Array.from(document.querySelectorAll('a,input[type=image],input[type=submit],button,input[type=button]')).map(el => ({
                    tag: el.tagName,
                    type: el.type || null,
                    text: (el.textContent || el.value || el.alt || '').trim().slice(0,40),
                    onclick: (el.onclick ? el.onclick.toString().slice(0,200) : (el.getAttribute('onclick') || '')).slice(0,200),
                    href: el.href || ''
                })).filter(x => x.text || x.onclick || x.href)"""
            )
            out.append("### 詳細ページのリンク・ボタン抜粋")
            out.append("```json")
            # 申込関連だけ抜粋
            interesting = [b for b in buttons if any(
                k in (b['text'] + b['onclick'] + b['href']) for k in ['申込','login','User','register','auth','myPage','メイン']
            )]
            out.append(json.dumps(interesting[:30], indent=2, ensure_ascii=False))
            out.append("```\n")
        except Exception as e:
            out.append(f"button列挙エラー: {e}\n")
            buttons = []

        # 申込関連のボタンをクリック
        out.append("## 申込手続き or 申込 をクリック試行")
        clicked = False
        for sel in [
            'a:has-text("申込手続き")',
            'a:has-text("申込")',
            'input[alt*="申込"]',
            'img[alt*="申込"]',
        ]:
            try:
                if popup.locator(sel).count() > 0:
                    out.append(f"- 試行: `{sel}`")
                    try:
                        with popup.expect_navigation(timeout=15000):
                            popup.click(sel, force=True)
                        popup.wait_for_load_state("domcontentloaded")
                        try:
                            popup.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        clicked = True
                        out.append(f"- 遷移先URL: `{popup.url}`")
                        out.append(f"- 遷移先title: `{popup.title()}`\n")
                        break
                    except Exception as e:
                        out.append(f"- クリック失敗: {e}\n")
            except Exception:
                continue

        if clicked:
            # ログイン画面のフォーム構造
            try:
                forms = popup.evaluate(
                    """() => Array.from(document.querySelectorAll('form')).map(f => ({
                        action: f.action,
                        method: f.method,
                        name: f.name,
                        inputs: Array.from(f.querySelectorAll('input,select,button')).map(i => ({
                            tag: i.tagName, type: i.type || null, name: i.name, id: i.id,
                            value: (i.value && i.value.length < 80) ? i.value : null
                        }))
                    }))"""
                )
                out.append("### 遷移先のフォーム")
                out.append("```json")
                out.append(json.dumps(forms, indent=2, ensure_ascii=False))
                out.append("```\n")
                # password input?
                pw = popup.evaluate(
                    """() => Array.from(document.querySelectorAll('input[type=password]')).map(el => ({
                        name: el.name, id: el.id
                    }))"""
                )
                out.append(f"### password input: `{pw}`")
                if pw:
                    out.append("- ★ ログイン画面到達")
            except Exception as e:
                out.append(f"フォーム取得エラー: {e}\n")

        browser.close()

    out_path = DOCS_DIR / "login_investigation2.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"OK: {out_path}")


if __name__ == "__main__":
    main()
