from pathlib import Path

from src.parser import has_results, parse_count, parse_listings

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_no_results_in_koganei_search():
    """小金井市の検索（現状0件）は空リスト。"""
    html = _read("search_result_koganei.html")
    assert has_results(html) is False
    assert parse_count(html) == 0
    assert parse_listings(html) == []


def test_no_results_in_empty_cluster():
    """空クラスタ(cz1)も0件扱い。"""
    html = _read("chizu_ref_cz1.html")
    assert has_results(html) is False
    assert parse_listings(html) == []


def test_parse_three_listings_from_cz9():
    """cz9（小平市・東大和市等）には3件あるはず。"""
    html = _read("chizu_ref_cz9.html")
    assert has_results(html) is True
    assert parse_count(html) == 3

    listings = parse_listings(html)
    assert len(listings) == 3

    # 1件目: 大和芝中
    first = listings[0]
    assert first.name == "大和芝中"
    assert first.address == "東大和市"
    assert first.layout == "2DKS"
    assert first.area_m2 == 51.74
    assert first.rent == 54100
    assert first.jkk_id == "5080040_0003"
    assert first.url and "jhomes" in first.url

    # 2件目: 大和上北台
    second = listings[1]
    assert second.name == "大和上北台"
    assert second.address == "東大和市"
    assert second.layout == "3K"
    assert second.area_m2 == 38.48
    assert second.rent == 43500
    assert second.jkk_id == "5080060_0003"

    # 3件目: 下里第二
    third = listings[2]
    assert third.name == "下里第二"
    assert third.address == "東久留米市"
    assert third.layout == "3DK"
    assert third.area_m2 == 63.38
    assert third.rent == 69900
    assert third.jkk_id == "5280040_0000"


def test_listing_keys_are_unique():
    """同一クラスタ内の物件キーがすべて違うこと。"""
    html = _read("chizu_ref_cz9.html")
    listings = parse_listings(html)
    keys = {listing.key for listing in listings}
    assert len(keys) == len(listings)


def test_count_and_listings_consistent_when_paginated():
    """ページングがある場合、表示件数 <= 総件数。

    cz10 は22件中1ページ10件表示など、デフォルトの showCount=10 だとページングが発生する。
    fetcher 側で showCount=50 や次ページ取得で対応する。
    """
    html = _read("chizu_ref_cz10.html")
    if has_results(html):
        listings = parse_listings(html)
        total = parse_count(html)
        assert len(listings) <= total
        assert len(listings) > 0
