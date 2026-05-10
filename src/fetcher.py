"""JKKねっと検索の実行。Playwright (headless) で popup-window と CSRF を自動処理する。

JKK仕様の特殊事情:
  検索条件フォームの市区町村チェック(`akiyaInitRM.akiyaRefM.checks`)は
  応答物件には反映されず、検索後の地図ページから czNo クラスタを選択する形でしか
  実物件は取得できない。よって本fetcherは:
    1. 検索フォーム → 地図ページ
    2. 地図上の各 czNo クラスタ(1-11)を順に開いて物件一覧を取得
    3. address が指定エリアに合致するもののみ返却（クライアント側フィルタ）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from playwright.sync_api import Page, sync_playwright

from .models import Listing
from .parser import has_results, parse_listings

START_URL = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
DEFAULT_CITY_MAP_PATH = Path("data/city_codes.json")
CZ_NUMBERS = list(range(1, 12))


class FetchError(RuntimeError):
    pass


def load_city_codes(path: Path = DEFAULT_CITY_MAP_PATH) -> dict[str, str]:
    if not path.exists():
        raise FetchError(
            f"city codes map not found: {path}. "
            "tools/build_city_map.py を実行して生成してください。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _open_search_popup(p) -> tuple:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        locale="ja-JP",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = ctx.new_page()
    with ctx.expect_page(timeout=15000) as popup_info:
        page.goto(START_URL, wait_until="load", timeout=30000)
    popup = popup_info.value
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    return browser, ctx, popup


def _go_to_map(popup: Page) -> None:
    """検索ボタンを押して地図ページに遷移する（条件は無設定でOK）。"""
    with popup.expect_navigation(timeout=20000):
        popup.click('a:has-text("検索")')
    popup.wait_for_load_state("domcontentloaded", timeout=15000)
    try:
        popup.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass


def _set_show_count(popup: Page, count: int) -> None:
    """ページ件数を増やしてパース時のページング負荷を減らす。"""
    try:
        popup.evaluate(
            f"""() => {{
                const sel = document.querySelector(
                    'select[name="akiyaRefRM.showCount"]'
                );
                if (sel) {{
                    sel.value = '{count}';
                    if (typeof reSearchPage === 'function') reSearchPage();
                }}
            }}"""
        )
        popup.wait_for_load_state("domcontentloaded", timeout=15000)
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
    except Exception:
        pass


def _fetch_cluster(popup: Page, cz_no: int) -> list[Listing]:
    """地図ページから czNo クラスタの物件一覧へ遷移して取得する。"""
    try:
        with popup.expect_navigation(timeout=20000):
            popup.evaluate(f"submitPage('{cz_no}')")
        popup.wait_for_load_state("domcontentloaded", timeout=15000)
        try:
            popup.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
    except Exception:
        return []

    html = popup.content()
    if not has_results(html):
        return []

    return parse_listings(html)


def _filter_by_areas(
    listings: list[Listing], areas: Iterable[str]
) -> list[Listing]:
    targets = {a.strip() for a in areas if a and a.strip()}
    if not targets:
        return listings
    return [l for l in listings if l.address and l.address.strip() in targets]


def fetch_listings(
    areas: Iterable[str],
    *,
    rent_max: Optional[int] = None,
    layouts: Optional[Iterable[str]] = None,
    area_min_m2: Optional[float] = None,
    headless: bool = True,
    city_codes_path: Path = DEFAULT_CITY_MAP_PATH,
) -> list[Listing]:
    """JKKねっとから空き家一覧を取得し、areas で住所フィルタした結果を返す。

    Args:
        areas: 監視対象の市区町村名（例: ["小金井市"]）
        rent_max: 家賃上限（円）。None なら制限なし
        layouts: 間取りリストでフィルタ（例: ["2DK", "3LDK"]）。None/空なら制限なし
        area_min_m2: 専有面積下限。None なら制限なし

    Returns:
        フィルタ通過した Listing のリスト
    """
    # city_codes は areas の妥当性検証用にロード
    code_map = load_city_codes(city_codes_path)
    target_areas = list(areas)
    unknown = [a for a in target_areas if a not in code_map]
    if unknown:
        raise FetchError(
            f"未知のエリア名: {unknown}. "
            f"data/city_codes.json で確認するか、tools/build_city_map.py で再生成してください。"
        )

    all_listings: list[Listing] = []

    with sync_playwright() as p:
        # JKK は no-cache 強制のため go_back が ERR_CACHE_MISS を起こす。
        # 各クラスタは fresh popup で取得する。
        for cz_no in CZ_NUMBERS:
            browser, _ctx, popup = _open_search_popup(p)
            try:
                _go_to_map(popup)
                cluster_listings = _fetch_cluster(popup, cz_no)
                all_listings.extend(cluster_listings)
            finally:
                browser.close()

    filtered = _filter_by_areas(all_listings, target_areas)

    if rent_max is not None:
        filtered = [l for l in filtered if l.rent <= rent_max]

    if area_min_m2 is not None:
        filtered = [
            l for l in filtered if l.area_m2 is not None and l.area_m2 >= area_min_m2
        ]

    if layouts:
        layout_set = {l.upper() for l in layouts}
        filtered = [
            l for l in filtered
            if l.layout and l.layout.upper() in layout_set
        ]

    return filtered
