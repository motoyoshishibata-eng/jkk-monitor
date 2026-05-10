"""JKKねっと検索結果HTMLから物件情報を抽出する。"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup

from .models import Listing

DETAIL_BASE = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"

_ZEN_TO_HAN = str.maketrans(
    {
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
        "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
        "Ａ": "A", "Ｂ": "B", "Ｃ": "C", "Ｄ": "D", "Ｅ": "E",
        "Ｆ": "F", "Ｇ": "G", "Ｈ": "H", "Ｉ": "I", "Ｊ": "J",
        "Ｋ": "K", "Ｌ": "L", "Ｍ": "M", "Ｎ": "N", "Ｏ": "O",
        "Ｐ": "P", "Ｑ": "Q", "Ｒ": "R", "Ｓ": "S", "Ｔ": "T",
        "Ｕ": "U", "Ｖ": "V", "Ｗ": "W", "Ｘ": "X", "Ｙ": "Y", "Ｚ": "Z",
        "．": ".",
    }
)

_SEN_PAGE_RE = re.compile(
    r"senPage\(\s*'[^']*'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\)"
)
_COUNT_RE = re.compile(r"(\d+)\s*件が該当しました")


def _to_half(s: str) -> str:
    return s.translate(_ZEN_TO_HAN)


def _clean(s: str) -> str:
    return s.strip().replace("　", " ").replace("\xa0", " ").strip()


def has_results(html: str) -> bool:
    """検索結果が0件でないかを判定。"""
    return "件が該当しました" in html


def parse_count(html: str) -> int:
    """『N件が該当しました』のNを抽出。なければ0。"""
    m = _COUNT_RE.search(html)
    return int(m.group(1)) if m else 0


def parse_listings(html: str) -> list[Listing]:
    """検索結果HTMLから Listing のリストを抽出する。0件の場合は空リスト。"""
    if not has_results(html):
        return []

    soup = BeautifulSoup(html, "lxml")
    listings: list[Listing] = []

    for tr in soup.find_all("tr", class_=re.compile(r"^ListTXT[12]$")):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 11:
            continue

        name = _clean(tds[1].get_text())
        address = _clean(tds[2].get_text())
        layout = _to_half(_clean(tds[5].get_text()))

        area_str = _clean(tds[6].get_text())
        try:
            area_m2: Optional[float] = float(area_str)
        except ValueError:
            area_m2 = None

        rent_str = _clean(tds[7].get_text()).replace(",", "")
        try:
            rent = int(rent_str)
        except ValueError:
            rent = 0

        jkk_id: Optional[str] = None
        offering_id = ""
        detail_link = tds[10].find("a")
        if detail_link and detail_link.get("onclick"):
            m = _SEN_PAGE_RE.search(detail_link["onclick"])
            if m:
                housing_id = m.group(2)
                offering_id = m.group(3)
                jkk_id = f"{housing_id}_{offering_id}"

        listings.append(
            Listing(
                name=name,
                room=offering_id,
                rent=rent,
                address=address,
                layout=layout,
                area_m2=area_m2,
                jkk_id=jkk_id,
                url=DETAIL_BASE,
            )
        )

    return listings
