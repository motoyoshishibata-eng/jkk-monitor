"""イベント日程（FOMC / 日銀）の読み込みと健全性チェック。

日付が1日ずれるだけで Phase 1・2 の結果は変わるので、読み込み時に
必ず構造チェックを掛け、未検証なら警告を返す。

指示書 v1.1 差分2: FOMC の声明発表時刻は **年間固定ではない**。
2013年3月13日に全定例会合が 14:00 ET に統一されるまでは、
記者会見のない会合は 14:15 ET だった。2007年起点の検証では
時期別テーブルで引く必要がある（定数直書きは禁止）。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pandas as pd

CONFIG = Path(__file__).resolve().parent.parent / "config"

# --- 差分2: 発表時刻の時期区分 ---
PC_ERA_START = _dt.date(2011, 4, 1)    # 記者会見の開始
UNIFIED_1400 = _dt.date(2013, 3, 13)   # 全定例会合が 14:00 ET に統一
ET_TO_SERVER_HOURS = 7                 # ET -> サーバー時間は年間固定で +7時間
                                       # (EDT=UTC-4 & サーバー=UTC+3 / EST=UTC-5 & サーバー=UTC+2)

UNKNOWN = "UNKNOWN"

_NULLISH = {"", "nan", "nat", "none", "<na>", "null", "unknown"}


def _norm_time(v) -> str:
    """CSV の時刻セルを "HH:MM" か "" に正規化する。"""
    t = str(v).strip()
    if t.lower() in _NULLISH:
        return ""
    parts = t.split(":")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1][:2].isdigit():
        return ""
    return f"{int(parts[0]):02d}:{int(parts[1][:2]):02d}"


def announcement_time_et(date, press_conference: str = "unknown") -> str | None:
    """声明の発表時刻(ET)を時期テーブルから引く。確定できなければ None。

    None が返る = 「要確認」であり、推測で埋めてはいけない（差分2）。
    呼び出し側はその会合をサンプルから除外し、除外件数を記録すること。
    """
    if isinstance(date, (pd.Timestamp, _dt.datetime)):
        date = date.date()
    pc = str(press_conference).strip().lower()
    if date >= UNIFIED_1400:
        return "14:00"
    if date < PC_ERA_START:
        return "14:15"          # 記者会見という制度が無い時期
    # 2011-04-01 〜 2013-03-12: 会見なし会合のみ 14:15 と分かっている
    return "14:15" if pc == "no" else None


def announcement_server_time(date, press_conference: str = "unknown") -> _dt.time | None:
    """声明発表のサーバー時刻。確定できなければ None。"""
    et = announcement_time_et(date, press_conference)
    if et is None:
        return None
    h, m = (int(x) for x in et.split(":"))
    return _dt.time((h + ET_TO_SERVER_HOURS) % 24, m)


def load_fomc(scheduled_only: bool = True, resolve_times: bool = True) -> pd.DataFrame:
    """FOMC日程を読む。

    resolve_times=True のとき、announcement_time_et 列が空の行を時期テーブルで
    埋め、サーバー時刻 announcement_server_time 列（datetime.time | NaT）を作る。
    CSV に明示された時刻が時期テーブルと食い違う場合は time_conflict=True を立てる
    （黙って上書きしない。突合ミスの検出用）。
    """
    df = pd.read_csv(CONFIG / "fomc_dates.csv", comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    for col, default in (("type", "scheduled"), ("press_conference", "unknown"),
                         ("sep", "unknown"), ("verified", "no"), ("source", "")):
        if col not in df.columns:
            df[col] = default
    df = df.sort_values("date").reset_index(drop=True)

    if resolve_times:
        expected = [announcement_time_et(d, pc)
                    for d, pc in zip(df["date"], df["press_conference"])]
        given = df.get("announcement_time_et", pd.Series([""] * len(df)))
        # pandas のバージョンによって空欄が NaN だったり文字列 "nan" だったりするので
        # 「時刻として読めない値はすべて空欄」に正規化する（黙って矛盾扱いしないため）
        given = pd.Series([_norm_time(v) for v in given], index=df.index)
        df["time_conflict"] = [(g != "" and e is not None and g != e)
                               for g, e in zip(given, expected)]
        df["announcement_time_et"] = [g if g else (e or "") for g, e in zip(given, expected)]
        df["announcement_server_time"] = [
            announcement_server_time(d, pc) if t else None
            for d, pc, t in zip(df["date"], df["press_conference"],
                                df["announcement_time_et"])]
        df["time_known"] = df["announcement_server_time"].notna()

    if scheduled_only:
        df = df[df["type"] == "scheduled"].reset_index(drop=True)
    return df


def load_boj() -> pd.DataFrame | None:
    """日銀金融政策決定会合。ファイルが無ければ None（Phase 2 で別掲をスキップ）。"""
    p = CONFIG / "boj_dates.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["date"])
    return df.sort_values("date").reset_index(drop=True) if len(df) else None


def audit_fomc() -> tuple[bool, list[str]]:
    """FOMC日程の構造チェック。出典との突き合わせの代わりにはならない。"""
    raw = load_fomc(scheduled_only=False)
    sch = raw[raw["type"] == "scheduled"]
    msgs, ok = [], True

    ver = raw["verified"].astype(str).str.lower()
    n_unver = int((ver == "no").sum())
    if n_unver:
        ok = False
        yrs = sorted(raw.loc[ver == "no", "date"].dt.year.unique())
        msgs.append(
            f"未突合の日付が {n_unver}/{len(raw)} 件（{yrs[0]}〜{yrs[-1]}年）。"
            "federalreserve.gov/monetarypolicy/fomc_historical.htm の年別ページと"
            "突き合わせて verified を yes にしてください。"
            "突合前の Phase 1/2 の結果は信用できません。")
    n_tent = int(ver.isin(["tentative", "scheduled"]).sum())
    if n_tent:
        msgs.append(f"暫定・未開催の日程が {n_tent} 件（verified=tentative/scheduled）。"
                    "直前会合で確定するまで動く可能性があります。")

    if "time_known" in raw.columns:
        n_unknown = int((~raw["time_known"]).sum())
        if n_unknown:
            yrs = sorted(raw.loc[~raw["time_known"], "date"].dt.year.unique())
            msgs.append(
                f"発表時刻が確定できない会合が {n_unknown} 件（{yrs[0]}〜{yrs[-1]}年）。"
                "press_conference が unknown のため時期テーブルで引けません。"
                "Phase 2 はこれらを自動で除外し、除外件数をレポートに残します。")
        if raw.get("time_conflict", pd.Series(dtype=bool)).any():
            ok = False
            bad = raw.loc[raw["time_conflict"], "date"].dt.date.tolist()
            msgs.append(f"CSV の発表時刻が時期テーブルと矛盾: {bad}。どちらが正しいか確認を。")

    per_year = sch.groupby(sch["date"].dt.year).size()
    bad_years = per_year[per_year != 8]
    if len(bad_years):
        ok = False
        msgs.append(f"定例会合が年8回でない年がある: {dict(bad_years)}")

    gaps = sch["date"].diff().dt.days.dropna()
    if len(gaps) and (gaps.min() < 30 or gaps.max() > 70):
        ok = False
        msgs.append(f"定例会合の間隔が想定外: min={gaps.min()}日 max={gaps.max()}日")

    if raw["date"].duplicated().any():
        ok = False
        msgs.append("日付の重複がある")

    if not msgs:
        msgs.append("構造チェックは全て通過（ただし出典との突き合わせは別途必要）。")
    return ok, msgs


def trading_day_index(dates) -> pd.DatetimeIndex:
    """データに実在する営業日（重複なし・昇順）。"""
    return pd.DatetimeIndex(sorted(set(pd.DatetimeIndex(dates).normalize())))


def fomc_cycle_day(tdays: pd.DatetimeIndex, fomc: pd.DatetimeIndex) -> pd.Series:
    """各営業日に FOMCサイクル日（営業日ベース）を付与する。

    Cieslak, Morse, Vissing-Jorgensen (2019) の定義に合わせ、
    day 0 = FOMC 声明発表日、day は営業日で数える。
    直近FOMCが休場日だった場合は次の営業日を day 0 とみなす。
    「次回FOMCの1営業日前」は day -1 として上書きする
    （偶数週0 が day -1〜3 と定義されているため）。
    """
    tdays = pd.DatetimeIndex(tdays)
    pos = tdays.searchsorted(pd.DatetimeIndex(fomc).normalize(), side="left")
    pos = sorted({int(p) for p in pos if 0 <= p < len(tdays)})
    if not pos:
        return pd.Series(index=tdays, dtype="float64")

    arr = pd.Series(index=tdays, dtype="float64")
    for i, start in enumerate(pos):
        end = pos[i + 1] if i + 1 < len(pos) else len(tdays)
        arr.iloc[start:end] = range(end - start)
    for start in pos:
        if start - 1 >= 0:
            arr.iloc[start - 1] = -1.0
    return arr
