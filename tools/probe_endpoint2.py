"""探査 第2段階: popup window を捕捉して検索フォームの構造を取得する。

JKKねっとの初期ページは onload で別window に POST するクラシックな作りなので、
Playwright の context.expect_page() で popup を待ち受け、その中身を解析する。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
TARGET_DOMAIN = "jhomes.to-kousya.or.jp"

requests_log: list[dict] = []


def _record_request(req) -> None:
    if TARGET_DOMAIN not in req.url:
        return
    requests_log.append(
        {
            "url": req.url,
            "method": req.method,
            "post_data": req.post_data,
            "resource_type": req.resource_type,
        }
    )


def main() -> None:
    out_lines: list[str] = []
    out_lines.append("# JKKねっと未ログイン探査結果（第2段）")
    out_lines.append(f"\n探査日時: {datetime.now().isoformat()}\n")

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
        context.on("request", _record_request)

        page = context.new_page()

        # popup を待ち受けながら初期ページに移動
        try:
            with context.expect_page(timeout=15000) as popup_info:
                page.goto(START_URL, wait_until="load", timeout=30000)
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception as e:
            out_lines.append(f"## エラー\npopup待ち受け失敗: `{e}`\n")
            # popup なしでもメイン画面に遷移しているかも
            popup = page

        # 少し追加で待つ（後続のJSリダイレクトに備えて）
        try:
            popup.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        final_url = popup.url
        title = popup.title()
        out_lines.append("## 検索画面（popup）の状態")
        out_lines.append(f"- URL: `{final_url}`")
        out_lines.append(f"- title: `{title}`")
        out_lines.append("")

        # フォーム情報
        try:
            forms = popup.evaluate(
                """() => Array.from(document.querySelectorAll('form')).map(f => ({
                    action: f.action,
                    method: f.method,
                    id: f.id,
                    name: f.name,
                    inputs: Array.from(f.querySelectorAll('input,select,textarea,button')).map(el => ({
                        tag: el.tagName,
                        type: el.type || null,
                        name: el.name,
                        id: el.id,
                        value: el.value && el.value.length < 100 ? el.value : null,
                    })),
                }))"""
            )
        except Exception as e:
            forms = [{"error": str(e)}]

        out_lines.append("## 検出フォーム")
        out_lines.append("```json")
        out_lines.append(json.dumps(forms, indent=2, ensure_ascii=False))
        out_lines.append("```\n")

        # select の option（市区町村コードを探す）
        try:
            selects = popup.evaluate(
                """() => Array.from(document.querySelectorAll('select')).map(s => ({
                    name: s.name,
                    id: s.id,
                    options: Array.from(s.options).map(o => ({
                        value: o.value, label: o.text,
                    })),
                }))"""
            )
        except Exception as e:
            selects = [{"error": str(e)}]

        out_lines.append("## select要素のoption一覧")
        out_lines.append("```json")
        out_lines.append(json.dumps(selects, indent=2, ensure_ascii=False))
        out_lines.append("```\n")

        # ラジオ・チェックボックスもチェック
        try:
            radios = popup.evaluate(
                """() => Array.from(document.querySelectorAll('input[type=radio],input[type=checkbox]')).map(el => ({
                    type: el.type, name: el.name, value: el.value,
                    label: (el.parentElement && el.parentElement.textContent) ? el.parentElement.textContent.trim().slice(0,50) : '',
                }))"""
            )
        except Exception as e:
            radios = [{"error": str(e)}]

        out_lines.append("## radio / checkbox 要素")
        out_lines.append("```json")
        out_lines.append(json.dumps(radios, indent=2, ensure_ascii=False))
        out_lines.append("```\n")

        # ナビゲーション履歴
        out_lines.append("## キャプチャしたリクエスト")
        for r in requests_log:
            out_lines.append(f"- {r['method']} `{r['url']}` ({r['resource_type']})")
            if r["post_data"]:
                out_lines.append(f"  - POST body: `{r['post_data'][:200]}`")
        out_lines.append("")

        # 全HTML保存
        try:
            html = popup.content()
            out_lines.append("## ページHTML（先頭8000文字）")
            out_lines.append("```html")
            out_lines.append(html[:8000])
            out_lines.append("```")
        except Exception as e:
            out_lines.append(f"HTML取得失敗: {e}")

        browser.close()

    out_path = DOCS_DIR / "endpoint_probe2.md"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    # 重要結果を ASCII で報告
    print(f"OK probe2 done: {out_path}")
    print(f"final URL: {final_url}")
    print(f"title: {title}")
    print(f"requests captured: {len(requests_log)}")


if __name__ == "__main__":
    main()
