"""
JKKねっと検索エンドポイント調査スクリプト（仕様書 §9）

使い方:
    pip install playwright
    playwright install chromium
    python tools/investigate_endpoint.py

動作:
    1. Playwright を headed モードで起動（ブラウザが開く）
    2. JKKねっとの空家検索ページを表示
    3. ネットワークトラフィックを記録開始
    4. ユーザーが手動で「小金井市」を選択して検索を実行
    5. 終わったらコンソールで Enter を押す
    6. 捕捉したリクエスト/レスポンスを docs/endpoint_analysis.md に書き出す
    7. 最終ページのHTMLを tests/fixtures/sample_response.html に保存
"""
import json
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
DOCS_DIR.mkdir(exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

SEARCH_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
TARGET_DOMAIN = "jhomes.to-kousya.or.jp"

requests_log: list[dict] = []
responses_log: list[dict] = []


def on_request(request) -> None:
    if TARGET_DOMAIN not in request.url:
        return
    requests_log.append(
        {
            "url": request.url,
            "method": request.method,
            "headers": dict(request.headers),
            "post_data": request.post_data,
            "resource_type": request.resource_type,
            "timestamp": datetime.now().isoformat(),
        }
    )


def on_response(response) -> None:
    if TARGET_DOMAIN not in response.url:
        return
    try:
        body = response.text()
        body_preview = body[:1000]
    except Exception:
        body_preview = "<binary or unavailable>"
    responses_log.append(
        {
            "url": response.url,
            "status": response.status,
            "headers": dict(response.headers),
            "body_preview": body_preview,
            "timestamp": datetime.now().isoformat(),
        }
    )


def write_markdown() -> Path:
    lines: list[str] = []
    lines.append("# JKKねっと検索エンドポイント調査結果")
    lines.append("")
    lines.append(f"調査日時: {datetime.now().isoformat()}")
    lines.append(f"対象ドメイン: `{TARGET_DOMAIN}`")
    lines.append("")
    lines.append(f"- 捕捉リクエスト数: {len(requests_log)}")
    lines.append(f"- 捕捉レスポンス数: {len(responses_log)}")
    lines.append("")

    lines.append("## リクエスト一覧")
    lines.append("")
    for i, req in enumerate(requests_log, 1):
        lines.append(f"### {i}. {req['method']} {req['url']}")
        lines.append(f"- resource_type: `{req['resource_type']}`")
        lines.append(f"- timestamp: {req['timestamp']}")
        if req["post_data"]:
            lines.append("- POSTボディ:")
            lines.append("```")
            lines.append(req["post_data"])
            lines.append("```")
        important_headers = {
            k: v
            for k, v in req["headers"].items()
            if k.lower() in ("content-type", "referer", "user-agent", "cookie", "origin")
        }
        if important_headers:
            lines.append("- ヘッダ抜粋:")
            lines.append("```json")
            lines.append(json.dumps(important_headers, indent=2, ensure_ascii=False))
            lines.append("```")
        lines.append("")

    lines.append("## レスポンス一覧")
    lines.append("")
    for i, resp in enumerate(responses_log, 1):
        lines.append(f"### {i}. {resp['status']} {resp['url']}")
        lines.append(f"- timestamp: {resp['timestamp']}")
        important_headers = {
            k: v
            for k, v in resp["headers"].items()
            if k.lower() in ("content-type", "set-cookie", "location")
        }
        if important_headers:
            lines.append("- ヘッダ抜粋:")
            lines.append("```json")
            lines.append(json.dumps(important_headers, indent=2, ensure_ascii=False))
            lines.append("```")
        lines.append("- ボディ先頭1000文字:")
        lines.append("```html")
        lines.append(resp["body_preview"])
        lines.append("```")
        lines.append("")

    out = DOCS_DIR / "endpoint_analysis.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> None:
    print("=" * 60)
    print("JKKねっと検索エンドポイント調査")
    print("=" * 60)
    print()
    print("操作手順:")
    print("  1. ブラウザが自動で開きます")
    print("  2. ログインが必要な場合はログインしてください")
    print("  3. 検索条件で「小金井市」を選択 → 検索ボタン")
    print("  4. 検索結果が表示されたら、可能なら物件1件の詳細も開いてください")
    print("  5. 終わったらこのコンソールで Enter を押してください")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(locale="ja-JP")
        page = context.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        try:
            page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"⚠ 初期ページの読み込みでタイムアウトまたはエラー: {e}")
            print("   ブラウザは開いたままです。手動でURLを開いて操作を続けてください。")

        input("→ 操作完了後、ここで Enter: ")

        try:
            html = page.content()
            (FIXTURES_DIR / "sample_response.html").write_text(html, encoding="utf-8")
            print(f"✓ 最終ページHTMLを保存: {FIXTURES_DIR / 'sample_response.html'}")
        except Exception as e:
            print(f"⚠ HTML取得失敗: {e}")

        # Cookieを保存（Phase 2 でログインセッション再利用するため）
        try:
            data_dir = ROOT / "data"
            data_dir.mkdir(exist_ok=True)
            context.storage_state(path=str(data_dir / "storage_state.json"))
            print(f"✓ Cookie/ストレージ状態を保存: {data_dir / 'storage_state.json'}")
        except Exception as e:
            print(f"⚠ ストレージ保存失敗: {e}")

        browser.close()

    out = write_markdown()
    print(f"\n✓ 調査結果Markdown: {out}")
    print(f"  リクエスト: {len(requests_log)} 件 / レスポンス: {len(responses_log)} 件")
    print()
    print("次のステップ:")
    print("  - docs/endpoint_analysis.md を開き、検索POSTのエンドポイント・パラメータを特定")
    print("  - 「小金井市」が内部コード（数字等）に変換されている場合はマッピング表を作成")
    print("  - その後 src/fetcher.py の実装に進む")


if __name__ == "__main__":
    main()
