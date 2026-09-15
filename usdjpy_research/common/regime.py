"""円高期 / 円安期 の区分。

指示書 §1 は「PROJECT.md の §4 決定表に記載済みの区分をそのまま流用。
新たに定義し直さない」と定めている。ここでいう PROJECT.md は
**既存EA開発リポジトリ**のもので（本パッケージの PREREGISTRATION.md ではない）、
本セッションからは参照できなかったため **暫定の区分** を置いている。

*** 必ず config/regimes.csv を 既存EA開発リポジトリの PROJECT.md §4 決定表の値で上書きすること。***
上書きしないまま出した円高期/円安期の別掲は、指示書の要件を満たさない。
暫定値のまま実行すると全レポートの先頭に警告が出る。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

CONFIG = Path(__file__).resolve().parent.parent / "config" / "regimes.csv"

PLACEHOLDER_FLAG = "PLACEHOLDER"


def load_regimes(path: str | Path | None = None) -> pd.DataFrame:
    df = pd.read_csv(path or CONFIG, comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    return df.sort_values("start").reset_index(drop=True)


def is_placeholder(df: pd.DataFrame | None = None) -> bool:
    df = load_regimes() if df is None else df
    return bool(df.get("source", pd.Series(dtype=str))
                .astype(str).str.contains(PLACEHOLDER_FLAG).any())


def label_series(index: pd.DatetimeIndex, regimes: pd.DataFrame | None = None) -> pd.Series:
    """各日時に円高期/円安期のラベルを付ける。どこにも入らなければ NaN。"""
    reg = load_regimes() if regimes is None else regimes
    out = pd.Series(pd.NA, index=index, dtype="object")
    for _, r in reg.iterrows():
        m = (index >= r["start"]) & (index <= r["end"])
        out[m] = r["label"]
    return out


def warning_banner() -> str:
    if is_placeholder():
        return ("!! 警告: 円高期/円安期の区分が暫定値のままです。"
                f"config/regimes.csv を既存EA開発リポジトリの PROJECT.md §4 決定表の区分で上書きしてから"
                "再実行してください。以下の期別集計は参考値に過ぎません。\n")
    return ""
