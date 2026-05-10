"""探査 第4段階: 地図ページから region をクリックして物件一覧へ。

probe3 の結果、検索後は地図ページが返り、submitPage(czNo) で
akiyaChizuRef へ遷移する仕様であることが判明。
小金井市は多摩地区なので czNo=11 もしくは近い番号が該当しそう。
全 czNo (1-11) を試して、それぞれの結果ページHTMLを保存する。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / "docs"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
DOCS_DIR.mkdir(exist_ok=True)
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
KOGANEI_VALUE = "40"


def search_with_filter(p, save_initial_html: bool = False):
    """検索条件画面 → 小金井市選択 → 検索実行 → 地図ページに到達した popup を返す。"""
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale="ja-JP")
    page = context.new_page()
    with context.expect_page(timeout=15000) as popup_info:
        page.goto(START_URL, wait_until="load", timeout=30000)
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    popup.check(
        f'input[name="akiyaInitRM.akiyaRefM.checks"][value="{KOGANEI_VALUE}"]'
    )
    popup.click('a:has-text("検索")')
    popup.wait_for_load_state("networkidle", timeout=20000)
    return browser, context, popup


def main() -> None:
    out_lines: list[str] = []
    out_lines.append("# 第4段探査: 地図 region クリックで物件一覧へ")
    out_lines.append(f"\n探査日時: {datetime.now().isoformat()}\n")

    with sync_playwright() as p:
        # 多摩地区 = 10, 11 を優先的に試す
        for cz_no in [11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]:
            browser, _ctx, popup = search_with_filter(p)
            try:
                with popup.expect_navigation(timeout=20000):
                    popup.evaluate(f"submitPage('{cz_no}')")
                # ナビ後にもう一段の遷移があるかも知れないので少し待つ
                popup.wait_for_load_state("domcontentloaded", timeout=15000)
                try:
                    popup.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
            except Exception as e:
                out_lines.append(f"\n## czNo={cz_no}: 遷移失敗: `{e}`")
                browser.close()
                continue

            url = popup.url
            try:
                title = popup.title()
            except Exception:
                title = "<取得失敗>"
            try:
                html = popup.content()
            except Exception as e:
                out_lines.append(
                    f"\n## czNo={cz_no}: content取得失敗: `{e}` (url={url})"
                )
                browser.close()
                continue
            has_koganei = "小金井" in html

            fname = FIXTURES_DIR / f"chizu_ref_cz{cz_no}.html"
            fname.write_text(html, encoding="utf-8")

            out_lines.append(f"\n## czNo={cz_no}")
            out_lines.append(f"- URL: `{url}`")
            out_lines.append(f"- title: `{title}`")
            out_lines.append(f"- 小金井 含む: **{has_koganei}**")
            out_lines.append(f"- HTMLサイズ: {len(html)} bytes")
            out_lines.append(f"- 保存先: `{fname.name}`")

            print(f"czNo={cz_no}: 小金井含={has_koganei} url={url}")
            browser.close()

    out_path = DOCS_DIR / "endpoint_probe4.md"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"\nOK probe4 done: {out_path}")


if __name__ == "__main__":
    main()
