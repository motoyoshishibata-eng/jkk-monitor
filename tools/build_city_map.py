"""市区町村名 → JKKコードのマップを抽出して data/city_codes.json に保存。

検索フォームの checkbox label からラベル文字列を取得する。
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="ja-JP")
        page = ctx.new_page()
        with ctx.expect_page(timeout=15000) as popup_info:
            page.goto(START_URL, wait_until="load", timeout=30000)
        popup = popup_info.value
        popup.wait_for_load_state("domcontentloaded", timeout=15000)
        try:
            popup.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # checkbox の隣にあるラベル文字列を取得
        # JKK のフォームは label タグを使わず、checkbox の直後に住所文字列が来る構造
        items = popup.evaluate(
            """() => {
                const cbs = Array.from(document.querySelectorAll(
                    'input[name="akiyaInitRM.akiyaRefM.checks"]'
                ));
                return cbs.map(cb => {
                    // 同じ td 内のテキストを取得 → checkbox の value 自身は除く
                    let text = '';
                    const td = cb.closest('td');
                    if (td) {
                        text = td.textContent.replace(/\\s+/g, ' ').trim();
                    }
                    return { value: cb.value, label: text };
                });
            }"""
        )

        browser.close()

    # ALLKU / ALLSI といった全選択 checkbox を除外
    mapping = {}
    for item in items:
        v = item["value"]
        label = item["label"]
        if v in ("ALLKU", "ALLSI") or v.startswith("ALL"):
            continue
        if not label or label == v:
            continue
        # 重複対策: 既存ラベルと値が違ったら警告（同じなら無視）
        if label in mapping and mapping[label] != v:
            print(f"⚠ 重複: '{label}' = {mapping[label]} と {v}")
        mapping[label] = v

    out = DATA_DIR / "city_codes.json"
    out.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"OK saved {len(mapping)} entries to {out}")
    if "小金井市" in mapping:
        print(f"  -> 小金井市 = {mapping['小金井市']}")


if __name__ == "__main__":
    main()
