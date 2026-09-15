"""サーバー時間 / タイムゾーン変換。

外為ファイネストMT5のサーバー時間は米国DSTに一致する前提:
  - GMT+3 : 3月第2日曜 〜 11月第1日曜（米国夏時間）
  - GMT+2 : 11月第1日曜 〜 3月第2日曜（米国標準時）

この前提は Phase 0-2（phases/phase0_dst.py）で実データ検証する。
検証に失敗した場合、ここの定数を書き換えるのではなく必ず報告すること。
"""

from __future__ import annotations

import datetime as _dt

# 前提となるオフセット（UTC からの時差）
SERVER_OFFSET_DST = 3   # 米国夏時間中
SERVER_OFFSET_STD = 2   # 米国標準時中

JST_OFFSET = 9
ET_OFFSET_DST = -4
ET_OFFSET_STD = -5


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """その月の n 番目の weekday(0=月,6=日) の日付。"""
    d = _dt.date(year, month, 1)
    shift = (weekday - d.weekday()) % 7
    return d + _dt.timedelta(days=shift + 7 * (n - 1))


def us_dst_start(year: int) -> _dt.date:
    """米国夏時間の開始日（3月第2日曜）。現行ルール(2007年以降)。"""
    return _nth_weekday(year, 3, 6, 2)


def us_dst_end(year: int) -> _dt.date:
    """米国夏時間の終了日（11月第1日曜）。現行ルール(2007年以降)。"""
    return _nth_weekday(year, 11, 6, 1)


def is_us_dst(d) -> bool:
    """その日が米国夏時間か。

    切替は現地 02:00 の日曜に起きるが、FX市場は日曜のNYオープン(17:00ET)から
    動くため、日付単位の判定で実務上は十分。境界日の扱いは
    「開始日の日曜は夏時間」「終了日の日曜は標準時」とする（週明けの
    月曜以降の判定は常に正しくなる）。
    """
    if isinstance(d, _dt.datetime):
        d = d.date()
    return us_dst_start(d.year) <= d < us_dst_end(d.year)


def server_offset(d) -> int:
    """その日のサーバーのUTCオフセット（+3 or +2）。"""
    return SERVER_OFFSET_DST if is_us_dst(d) else SERVER_OFFSET_STD


def server_to_utc(dt: _dt.datetime) -> _dt.datetime:
    return dt - _dt.timedelta(hours=server_offset(dt))


def server_to_jst(dt: _dt.datetime) -> _dt.datetime:
    return server_to_utc(dt) + _dt.timedelta(hours=JST_OFFSET)


def server_to_et(dt: _dt.datetime) -> _dt.datetime:
    utc = server_to_utc(dt)
    off = ET_OFFSET_DST if is_us_dst(dt) else ET_OFFSET_STD
    return utc + _dt.timedelta(hours=off)


# --- 米国イベントのサーバー時間（年間固定になるはずの値） ---
FOMC_SERVER_HOUR = 21        # 14:00 ET
NY_OPTION_CUT_SERVER_HOUR = 17   # 10:00 ET
NFP_SERVER_HOUR, NFP_SERVER_MINUTE = 15, 30   # 08:30 ET

# --- 東京基準（サーバー時間が夏冬で1時間ずれる） ---
def tokyo_fix_server_hm(d) -> tuple[int, int]:
    """東京仲値 09:55 JST のサーバー時刻 (hour, minute)。夏03:55 / 冬02:55。"""
    return (3, 55) if is_us_dst(d) else (2, 55)


def uk_dst_start(year: int) -> _dt.date:
    """英国夏時間(BST)の開始日: 3月最終日曜。"""
    d = _dt.date(year, 3, 31)
    return d - _dt.timedelta(days=(d.weekday() + 1) % 7)


def uk_dst_end(year: int) -> _dt.date:
    """英国夏時間(BST)の終了日: 10月最終日曜。"""
    d = _dt.date(year, 10, 31)
    return d - _dt.timedelta(days=(d.weekday() + 1) % 7)


def is_uk_dst(d) -> bool:
    if isinstance(d, _dt.datetime):
        d = d.date()
    return uk_dst_start(d.year) <= d < uk_dst_end(d.year)


def london_fix_server_hour(d) -> int:
    """ロンドン16時FIX のサーバー時刻（時）。

    英国DSTと米国DSTは切替日が違うため、年2回「ズレ期間」が発生する:
      - 3月第2日曜〜3月最終日曜 : 米=夏 / 英=冬 -> 19:00
      - 10月最終日曜〜11月第1日曜: 米=夏 / 英=冬 -> 19:00
      - それ以外（両方夏 / 両方冬）                -> 18:00
    指示書 §0-2 の「変動（秋ズレあり）」はこれを指す。
    """
    utc_hour = 15 if is_uk_dst(d) else 16
    return utc_hour + server_offset(d)
