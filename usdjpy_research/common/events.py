"""イベント日程（FOMC / 日銀）の読み込みと健全性チェック。

日付が1日ずれるだけで Phase 1・2 の結果は変わるので、読み込み時に
必ず構造チェックを掛け、未検証なら警告を返す。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

CONFIG = Path(__file__).resolve().parent.parent / "config"


def load_fomc(scheduled_only: bool = True) -> pd.DataFrame:
    df = pd.read_csv(CONFIG / "fomc_dates.csv", comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df[df["type"] == "scheduled"].reset_index(drop=True) if scheduled_only else df


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

    unverified = int((raw.get("verified", pd.Series(["no"] * len(raw)))
                      .astype(str).str.lower() != "yes").sum())
    if unverified:
        ok = False
        msgs.append(
            f"未検証の日付が {unverified}/{len(raw)} 件。federalreserve.gov の "
            "会合カレンダーと突き合わせ、config/fomc_dates.csv の verified 列を "
            "yes にしてください。突き合わせ前の Phase 1/2 の結果は信用できません。")

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
    p = pd.Series(pos)
    for i, start in enumerate(pos):
        end = pos[i + 1] if i + 1 < len(pos) else len(tdays)
        n = end - start
        arr.iloc[start:end] = range(n)
    # FOMC 前日(1営業日前) を day -1 に
    for start in pos:
        if start - 1 >= 0:
            arr.iloc[start - 1] = -1.0
    return arr
