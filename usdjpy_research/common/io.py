"""MT5 エクスポートCSV および補助データの読み込み。

MT5 の「銘柄 -> バー -> エクスポート」で出る CSV は概ね次の形式:

    <DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>
    2024.01.02\t00:00:00\t141.041\t141.055\t141.035\t141.048\t53\t0\t14

区切り文字・列名・列数はビルドや設定で変わるため、ここでは厳密一致を
要求せず「それらしい列」を拾う実装にしている。読み込み結果は必ず
`describe_bars()` で目視確認すること（黙って間違ったデータで回すのが
最悪のパターンなので）。

時刻はすべて **サーバー時間の naive datetime** として扱う。
tz-aware に変換しないのは、Phase 1〜3 が「サーバー時間の固定時刻」を
直接使うためで、変換すると DST 処理のバグが入り込む余地が増えるから。
"""

from __future__ import annotations

import io as _io
from pathlib import Path

import numpy as np
import pandas as pd

_ALIASES = {
    "date": "date", "<date>": "date",
    "time": "time", "<time>": "time",
    "datetime": "datetime", "<datetime>": "datetime", "timestamp": "datetime",
    "open": "open", "<open>": "open", "o": "open",
    "high": "high", "<high>": "high", "h": "high",
    "low": "low", "<low>": "low", "l": "low",
    "close": "close", "<close>": "close", "c": "close",
    "tickvol": "tickvol", "<tickvol>": "tickvol", "tick_volume": "tickvol",
    "vol": "vol", "<vol>": "vol", "volume": "vol", "real_volume": "vol",
    "spread": "spread", "<spread>": "spread",
}

REQUIRED = ("open", "high", "low", "close")


def _sniff_sep(head: str) -> str:
    counts = {sep: head.count(sep) for sep in ("\t", ";", ",")}
    sep, n = max(counts.items(), key=lambda kv: kv[1])
    return sep if n else ","


def load_mt5_bars(path: str | Path, tz_note: bool = True) -> pd.DataFrame:
    """MT5 バーCSVを読み、サーバー時間 index の DataFrame を返す。

    返り値の index は昇順・重複なしの naive DatetimeIndex（サーバー時間）。
    列は open/high/low/close と、あれば tickvol/vol/spread。
    """
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    sep = _sniff_sep(raw.split("\n", 1)[0])
    df = pd.read_csv(_io.StringIO(raw), sep=sep, engine="python")
    df.columns = [_ALIASES.get(str(c).strip().lower(), str(c).strip().lower())
                  for c in df.columns]

    if "datetime" in df.columns:
        idx = pd.to_datetime(df["datetime"], errors="coerce")
    elif "date" in df.columns and "time" in df.columns:
        idx = pd.to_datetime(df["date"].astype(str).str.strip() + " "
                             + df["time"].astype(str).str.strip(),
                             format="mixed", errors="coerce")
    elif "date" in df.columns:
        idx = pd.to_datetime(df["date"], errors="coerce")
    else:
        raise ValueError(
            f"{path}: 日時列が見つからない。先頭行: {raw.split(chr(10),1)[0]!r}")

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: 必須列が無い {missing} / 実際の列={list(df.columns)}")

    keep = [c for c in ("open", "high", "low", "close", "tickvol", "vol", "spread")
            if c in df.columns]
    out = df[keep].apply(pd.to_numeric, errors="coerce")
    out.index = idx
    out = out[out.index.notna()]
    out = out[out["close"].notna()]
    out = out[~out.index.duplicated(keep="first")].sort_index()
    out.index.name = "server_time"
    if tz_note:
        out.attrs["timezone"] = "broker server time (GMT+3 / GMT+2, US DST)"
    out.attrs["source_file"] = str(path)
    return out


def infer_point_size(df: pd.DataFrame) -> float:
    """価格の刻み幅（point）を推定する。USDJPY は 0.001 か 0.01。

    MT5 の <SPREAD> 列は point 単位なので、pips 換算にこれが要る。
    """
    px = df["close"].dropna()
    if px.empty:
        return 0.001
    # 小数第3位に有効数字があるか
    frac3 = np.round(px.to_numpy() * 1000) % 10
    return 0.001 if np.count_nonzero(frac3) > 0.01 * len(px) else 0.01


PIP = 0.01  # USDJPY の 1 pip


def points_per_pip(df: pd.DataFrame) -> float:
    return PIP / infer_point_size(df)


def describe_bars(df: pd.DataFrame) -> str:
    """読み込み結果の要約。必ず目視すること。"""
    pt = infer_point_size(df)
    gaps = df.index.to_series().diff().dropna()
    mode = gaps.mode()
    lines = [
        f"file      : {df.attrs.get('source_file','?')}",
        f"rows      : {len(df):,}",
        f"period    : {df.index.min()} 〜 {df.index.max()} (サーバー時間)",
        f"bar step  : {mode.iloc[0] if len(mode) else '?'}（最頻値）",
        f"columns   : {list(df.columns)}",
        f"point size: {pt}  -> 1pip = {PIP/pt:.0f} points",
        f"price rng : {df['close'].min():.3f} 〜 {df['close'].max():.3f}",
        f"NaN close : {int(df['close'].isna().sum())}",
    ]
    if "spread" in df.columns:
        sp = df["spread"].dropna()
        if len(sp):
            lines.append(
                f"spread col: 中央値 {sp.median():.0f} points "
                f"= {sp.median()*pt/PIP:.2f} pips（0 ばかりなら実測に使えない）")
    yearly = df.groupby(df.index.year).size()
    lines.append("bars/year : " + ", ".join(f"{y}:{n:,}" for y, n in yearly.items()))
    return "\n".join(lines)


def load_series_csv(path: str | Path, value_col: str | None = None) -> pd.Series:
    """日付 + 値 の汎用CSV（株価指数・建玉など）を Series で読む。"""
    path = Path(path)
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    sep = _sniff_sep(raw.split("\n", 1)[0])
    df = pd.read_csv(_io.StringIO(raw), sep=sep, engine="python")
    df.columns = [str(c).strip().lower() for c in df.columns]
    date_col = next((c for c in df.columns if c in ("date", "日付", "datetime", "<date>")),
                    df.columns[0])
    if value_col is None:
        cand = [c for c in df.columns if c != date_col]
        value_col = next((c for c in cand if c in ("close", "adj close", "終値", "value")),
                         cand[0])
    s = pd.Series(pd.to_numeric(df[value_col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df[date_col], errors="coerce"), name=value_col)
    s = s[s.index.notna()].dropna()
    return s[~s.index.duplicated(keep="first")].sort_index()
