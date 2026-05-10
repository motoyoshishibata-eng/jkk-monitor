"""ログイン画面の送信ボタン構造の詳細調査。"""
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
    out.append(f"# ログイン画面ボタン詳細調査 ({datetime.now().isoformat()})\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
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

        # 検索 → cz9 → 詳細 → 申込
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

        out.append(f"ログイン画面 URL: `{popup.url}`")
        out.append(f"title: `{popup.title()}`\n")

        # form 内外問わず、すべてのクリック可能要素を列挙
        elements = popup.evaluate(
            """() => {
                const els = document.querySelectorAll('a, input, button, img');
                return Array.from(els).map(el => ({
                    tag: el.tagName,
                    type: el.type || null,
                    name: el.name || null,
                    src: el.src || null,
                    alt: el.alt || null,
                    text: (el.textContent || el.value || '').trim().slice(0, 50),
                    onclick: (el.getAttribute('onclick') || '').slice(0, 200),
                    href: el.href || null,
                    parent_form: el.closest('form') ? el.closest('form').name : null
                }));
            }"""
        )
        # ログイン関連だけ抜粋
        relevant = [
            e for e in elements
            if any(
                k in (str(e.get('text','')) + str(e.get('alt','')) + str(e.get('onclick','')) + str(e.get('src','')) + str(e.get('href','')))
                for k in ['ログイン', 'login', 'Login', 'submit', 'login_btn', 'btnLogin', 'btn_log']
            )
        ]
        out.append("## ログイン関連の要素")
        out.append("```json")
        out.append(json.dumps(relevant, indent=2, ensure_ascii=False))
        out.append("```\n")

        # ninsyologinForm 直下の全要素
        form_elements = popup.evaluate(
            """() => {
                const f = document.ninsyologinForm;
                if (!f) return null;
                const all = f.querySelectorAll('*');
                return {
                    action: f.action,
                    method: f.method,
                    children: Array.from(all).map(el => ({
                        tag: el.tagName,
                        type: el.type || null,
                        name: el.name || null,
                        alt: el.alt || null,
                        src: el.src || null,
                        onclick: (el.getAttribute('onclick') || '').slice(0, 200),
                        href: el.href || null
                    }))
                };
            }"""
        )
        out.append("## ninsyologinForm 配下の全要素")
        out.append("```json")
        out.append(json.dumps(form_elements, indent=2, ensure_ascii=False))
        out.append("```\n")

        # ページ全体のJS関数定義を一部抽出（submitAction系）
        scripts = popup.evaluate(
            """() => {
                const scripts = document.querySelectorAll('script');
                return Array.from(scripts)
                    .map(s => s.textContent || '')
                    .filter(t => /login|submitAction|ninsyo/i.test(t))
                    .map(t => t.slice(0, 500));
            }"""
        )
        out.append("## ログイン関連JS（先頭500文字）")
        out.append("```javascript")
        out.append("\n---\n".join(scripts[:5]))
        out.append("```\n")

        browser.close()

    out_path = DOCS_DIR / "login_investigation3.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"OK: {out_path}")


if __name__ == "__main__":
    main()
