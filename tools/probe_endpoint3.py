"""探査 第3段階: 小金井市のみで検索を実行し、結果ページの構造を取得する。

probe2 で得た情報:
  - form name = akiSearch (POST)
  - 小金井市 checkbox value = "40" (name = akiyaInitRM.akiyaRefM.checks)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
DOCS_DIR.mkdir(exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
TARGET_DOMAIN = "jhomes.to-kousya.or.jp"
KOGANEI_VALUE = "40"

requests_log: list[dict] = []
responses_log: list[dict] = []


def _on_request(req) -> None:
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


def _on_response(resp) -> None:
    if TARGET_DOMAIN not in resp.url:
        return
    try:
        text = resp.text()[:2000]
    except Exception:
        text = "<binary>"
    responses_log.append(
        {
            "url": resp.url,
            "status": resp.status,
            "headers": dict(resp.headers),
            "body_preview": text,
        }
    )


def main() -> None:
    out_lines: list[str] = []
    out_lines.append("# 第3段探査: 小金井市で検索実行")
    out_lines.append(f"\n探査日時: {datetime.now().isoformat()}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="ja-JP")
        context.on("request", _on_request)
        context.on("response", _on_response)
        page = context.new_page()

        # popup を待ち受けて検索画面へ
        with context.expect_page(timeout=15000) as popup_info:
            page.goto(START_URL, wait_until="load", timeout=30000)
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded", timeout=15000)
        try:
            popup.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        out_lines.append(f"## 検索画面 URL\n`{popup.url}`\n")

        # 小金井市のチェックボックスを ON にする
        # name="akiyaInitRM.akiyaRefM.checks" value="40"
        try:
            popup.check(
                'input[name="akiyaInitRM.akiyaRefM.checks"][value="40"]'
            )
            out_lines.append("- 小金井市 (value=40) のチェックを ON にしました\n")
        except Exception as e:
            out_lines.append(f"- ⚠ チェック失敗: {e}\n")

        # 検索ボタンを探してクリック
        # フォームに submit ボタンがあるはず
        try:
            buttons = popup.evaluate(
                """() => Array.from(document.querySelectorAll('input[type=submit],input[type=button],button')).map(b => ({
                    type: b.type, name: b.name, value: b.value, text: b.textContent || b.value
                }))"""
            )
            out_lines.append("## ボタン一覧")
            out_lines.append("```json")
            out_lines.append(json.dumps(buttons, indent=2, ensure_ascii=False))
            out_lines.append("```\n")
        except Exception as e:
            out_lines.append(f"ボタン取得失敗: {e}\n")
            buttons = []

        # 「検索」を含むボタンか、submit ボタンをクリック
        clicked = False
        for sel in [
            'input[type=submit][value*="検索"]',
            'button:has-text("検索")',
            'input[type=image][alt*="検索"]',
            'a:has-text("検索")',
        ]:
            try:
                if popup.locator(sel).count() > 0:
                    popup.click(sel, timeout=5000)
                    clicked = True
                    out_lines.append(f"- 検索クリック: `{sel}`\n")
                    break
            except Exception:
                continue

        if not clicked:
            # Form submit を JS で直接実行
            try:
                popup.evaluate("document.akiSearch.submit()")
                clicked = True
                out_lines.append("- akiSearch.submit() を JS で実行\n")
            except Exception as e:
                out_lines.append(f"- ⚠ form submit 失敗: {e}\n")

        # 結果ページの読み込みを待つ
        try:
            popup.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        result_url = popup.url
        out_lines.append(f"## 検索結果 URL\n`{result_url}`\n")

        # 結果ページHTMLをfixture保存
        try:
            html = popup.content()
            (FIXTURES_DIR / "search_result_koganei.html").write_text(
                html, encoding="utf-8"
            )
            out_lines.append(
                f"- HTMLを保存: `tests/fixtures/search_result_koganei.html` "
                f"({len(html)} bytes)\n"
            )
        except Exception as e:
            out_lines.append(f"⚠ HTML保存失敗: {e}\n")

        # 検索POSTのリクエストボディを抽出
        out_lines.append("## キャプチャしたPOSTリクエスト")
        for r in requests_log:
            if r["method"] == "POST":
                out_lines.append(f"- `{r['url']}`")
                if r["post_data"]:
                    out_lines.append("  - body:")
                    out_lines.append("    ```")
                    out_lines.append(f"    {r['post_data'][:1500]}")
                    out_lines.append("    ```")
        out_lines.append("")

        # 直近のレスポンス
        out_lines.append("## 直近レスポンス")
        for resp in responses_log[-3:]:
            out_lines.append(f"- {resp['status']} `{resp['url']}`")
            ct = resp["headers"].get("content-type", "")
            out_lines.append(f"  - content-type: `{ct}`")
        out_lines.append("")

        browser.close()

    out_path = DOCS_DIR / "endpoint_probe3.md"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"OK probe3 done: {out_path}")
    print(f"final URL: {result_url}")


if __name__ == "__main__":
    main()
