"""JKKねっとのログインフロー調査（無人）。

調査内容:
  1. JKK公式トップ (https://www.to-kousya.or.jp/) からログインリンクを探す
  2. ログイン画面のフォーム構造を取得
  3. 結果を docs/login_investigation.md に保存
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

KOUSYA_TOP = "https://www.to-kousya.or.jp/"
JHOMES_TOP = "https://jhomes.to-kousya.or.jp/"


def main() -> None:
    out: list[str] = []
    out.append(f"# JKKログイン調査 ({datetime.now().isoformat()})\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()

        # 1. 公社トップから「ログイン」「マイページ」リンクを探す
        out.append("## 1. https://www.to-kousya.or.jp/ のログイン関連リンク\n")
        try:
            page.goto(KOUSYA_TOP, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            links = page.evaluate(
                """() => Array.from(document.querySelectorAll('a')).map(a => ({
                    text: (a.textContent || '').trim().slice(0, 50),
                    href: a.href
                })).filter(x => /ログイン|login|マイページ|mypage|jkknet/i.test(x.text + x.href))"""
            )
            out.append("```json")
            out.append(json.dumps(links, indent=2, ensure_ascii=False))
            out.append("```\n")
        except Exception as e:
            out.append(f"エラー: {e}\n")
            links = []

        # 2. jhomes 直下のリンクも調査
        out.append("## 2. jhomes トップのリンク\n")
        try:
            page.goto(JHOMES_TOP, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            jhomes_links = page.evaluate(
                """() => Array.from(document.querySelectorAll('a')).map(a => ({
                    text: (a.textContent || '').trim().slice(0, 50),
                    href: a.href
                }))"""
            )
            out.append("```json")
            out.append(json.dumps(jhomes_links[:30], indent=2, ensure_ascii=False))
            out.append("```\n")
        except Exception as e:
            out.append(f"エラー: {e}\n")
            jhomes_links = []

        # 3. 推測されるログインURLを試す
        candidate_urls = [
            "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaUserLoginInit",
            "https://jhomes.to-kousya.or.jp/search/jkknet/service/loginInit",
            "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaUserLogin",
            "https://jhomes.to-kousya.or.jp/search/jkknet/service/userLoginInit",
        ]
        # 既知のログインリンクから推測URLを追加
        for link in (links or []) + (jhomes_links or []):
            href = link.get("href", "")
            if "jhomes" in href and href not in candidate_urls:
                candidate_urls.append(href)

        out.append("## 3. ログインURL候補へのアクセス試行\n")
        for url in candidate_urls:
            try:
                # popup 構造の可能性に備えて expect_page も用意
                test_page = ctx.new_page()
                popup = None
                try:
                    with ctx.expect_page(timeout=5000) as popup_info:
                        test_page.goto(
                            url, wait_until="domcontentloaded", timeout=15000
                        )
                    popup = popup_info.value
                    popup.wait_for_load_state("domcontentloaded", timeout=10000)
                    try:
                        popup.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    target = popup
                except Exception:
                    target = test_page

                final_url = target.url
                title = target.title()
                # ログインフォームらしき password 入力があるか
                pw_inputs = target.evaluate(
                    """() => Array.from(document.querySelectorAll('input[type=password]')).map(el => ({
                        name: el.name, id: el.id
                    }))"""
                )
                forms = target.evaluate(
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
                out.append(f"### {url}")
                out.append(f"- 最終URL: `{final_url}`")
                out.append(f"- title: `{title}`")
                out.append(f"- password input: `{pw_inputs}`")
                if pw_inputs:
                    out.append("- ★ ログイン画面の可能性大")
                    out.append("- forms (一部抜粋):")
                    out.append("```json")
                    out.append(
                        json.dumps(forms[:2], indent=2, ensure_ascii=False)
                    )
                    out.append("```")
                out.append("")
                if popup:
                    popup.close()
                test_page.close()
            except Exception as e:
                out.append(f"### {url}\n- エラー: {e}\n")

        browser.close()

    out_path = DOCS_DIR / "login_investigation.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"OK: {out_path}")


if __name__ == "__main__":
    main()
