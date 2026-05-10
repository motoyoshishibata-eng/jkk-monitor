"""未ログイン状態でJKKあき家検索ページがどこまで触れるかの自動探査。

ログイン不要で検索可能なら §9 調査をここで完結できる可能性がある。
ログイン必須なら、そのことを確認して investigate_endpoint.py の手動実行に進む。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

SEARCH_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
TARGET_DOMAIN = "jhomes.to-kousya.or.jp"

navigations: list[dict] = []
forms_info: list[dict] = []
selects_info: list[dict] = []


def main() -> None:
    out_lines: list[str] = []
    out_lines.append("# JKKねっと未ログイン探査結果")
    out_lines.append("")
    out_lines.append(f"探査日時: {datetime.now().isoformat()}")
    out_lines.append("")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        page.on(
            "framenavigated",
            lambda frame: navigations.append(
                {"url": frame.url, "timestamp": datetime.now().isoformat()}
            ),
        )

        try:
            response = page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
            status = response.status if response else None
            final_url = page.url
            title = page.title()
        except Exception as e:
            out_lines.append(f"## エラー\nページ読み込みに失敗: `{e}`\n")
            (DOCS_DIR / "endpoint_probe.md").write_text(
                "\n".join(out_lines), encoding="utf-8"
            )
            print(f"⚠ 失敗: {e}")
            browser.close()
            return

        out_lines.append("## 初期ナビゲーション")
        out_lines.append(f"- リクエストURL: `{SEARCH_URL}`")
        out_lines.append(f"- 最終URL: `{final_url}`")
        out_lines.append(f"- HTTP status: `{status}`")
        out_lines.append(f"- ページtitle: `{title}`")
        out_lines.append("")

        redirected_to_login = (
            final_url != SEARCH_URL
            and ("login" in final_url.lower() or "Login" in final_url)
        )
        login_keywords_in_title = any(
            k in title for k in ("ログイン", "login", "Login")
        )

        out_lines.append("## ログイン要否の判定")
        if redirected_to_login or login_keywords_in_title:
            out_lines.append("- 判定: **ログイン必須の可能性が高い**")
            out_lines.append(
                f"- 理由: 最終URL={final_url}, title={title}"
            )
        else:
            out_lines.append("- 判定: ログインなしでアクセスできた可能性")
        out_lines.append("")

        # フォーム構造の抽出
        try:
            forms = page.evaluate(
                """() => {
                    return Array.from(document.querySelectorAll('form')).map(f => ({
                        action: f.action,
                        method: f.method,
                        id: f.id,
                        name: f.name,
                        inputs: Array.from(f.querySelectorAll('input,select,textarea')).map(i => ({
                            tag: i.tagName,
                            type: i.type || null,
                            name: i.name,
                            id: i.id,
                        })),
                    }));
                }"""
            )
            forms_info.extend(forms)
        except Exception as e:
            forms_info.append({"error": str(e)})

        out_lines.append("## 検出フォーム")
        out_lines.append("```json")
        out_lines.append(json.dumps(forms_info, indent=2, ensure_ascii=False))
        out_lines.append("```")
        out_lines.append("")

        # selectのoption一覧
        try:
            selects = page.evaluate(
                """() => {
                    return Array.from(document.querySelectorAll('select')).map(s => ({
                        name: s.name,
                        id: s.id,
                        options: Array.from(s.options).map(o => ({
                            value: o.value,
                            label: o.text,
                        })),
                    }));
                }"""
            )
            selects_info.extend(selects)
        except Exception as e:
            selects_info.append({"error": str(e)})

        out_lines.append("## select要素のoption一覧（市区町村コードの手がかり）")
        out_lines.append("```json")
        out_lines.append(json.dumps(selects_info, indent=2, ensure_ascii=False))
        out_lines.append("```")
        out_lines.append("")

        # 全ナビゲーション履歴
        out_lines.append("## ナビゲーション履歴")
        for nav in navigations:
            out_lines.append(f"- {nav['timestamp']} → {nav['url']}")
        out_lines.append("")

        # ページHTMLも保存（先頭5KB）
        try:
            html = page.content()
            out_lines.append("## ページHTML（先頭5000文字）")
            out_lines.append("```html")
            out_lines.append(html[:5000])
            out_lines.append("```")
        except Exception as e:
            out_lines.append(f"⚠ HTML取得失敗: {e}")

        browser.close()

    out_path = DOCS_DIR / "endpoint_probe.md"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"✓ 探査完了: {out_path}")
    print(f"  最終URL: {final_url}")
    print(f"  status: {status}")
    print(f"  title: {title}")
    print(f"  検出フォーム: {len(forms_info)} 件")
    print(f"  検出select: {len(selects_info)} 件")


if __name__ == "__main__":
    main()
