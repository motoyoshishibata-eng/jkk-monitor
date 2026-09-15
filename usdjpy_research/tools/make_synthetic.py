"""【スモークテスト専用】合成USDJPYバーを生成する。

*** これは実データではない。ここから出た統計値に意味は一切ない。***

目的はただ一つ、「手元のファイネストCSVを入れる前に、パイプラインが
最後までエラー無く走ることを確認する」こと。
実データが手に入ったら、このファイルが作った CSV は捨てること。

合成データには次の構造だけを意図的に埋め込んである（Phase 0-2 の検証が
正しく機能することを確かめるため）:
  - 東京仲値の出来高スパイク（夏 03:55 / 冬 02:55 サーバー時間）
  - 米雇用統計の出来高スパイク（第1金曜 15:30 サーバー時間, 年間固定）
  - ロンドン16時FIX の出来高スパイク（通常期 18:00 / 英米DSTギャップ期間 19:00）
  - 時間帯依存のスプレッド
価格は方向性のないランダムウォークなので、Phase 1〜6 は
「不合格」が出るのが正しい挙動。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from usdjpy_research.common import tz  # noqa: E402

SYNTHETIC_HEADER = "# SYNTHETIC DATA - NOT REAL MARKET DATA - DO NOT USE FOR DECISIONS"


def build(start: str, end: str, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="min")
    idx = idx[idx.dayofweek < 5]                      # 月〜金のみ（FX週に合わせる）

    hour = idx.hour.to_numpy()
    minute = idx.minute.to_numpy()
    dates = pd.Series(idx.date, index=idx)
    dst = dates.map(tz.is_us_dst).to_numpy()

    # 時間帯別のボラ形状（東京・ロンドン・NYの三山）
    shape = (0.25 + 0.6 * np.exp(-((hour - 3) ** 2) / 6)
             + 1.0 * np.exp(-((hour - 11) ** 2) / 8)
             + 1.2 * np.exp(-((hour - 17) ** 2) / 8))
    step = rng.normal(0.0, 0.006, idx.size) * shape
    close = 140.0 + np.cumsum(step)
    spread_px = np.abs(rng.normal(0.0, 0.004, idx.size)) * shape
    high = close + spread_px
    low = close - spread_px
    open_ = np.concatenate([[close[0]], close[:-1]])

    # tickvol: 形状 + 東京仲値スパイク + 雇用統計スパイク
    vol = shape * 40 * rng.gamma(3.0, 1 / 3.0, idx.size)
    fix_h = np.where(dst, 3, 2)
    vol += np.where((hour == fix_h) & (minute == 55), 900.0, 0.0)
    first_fri = dates.map(lambda d: d.weekday() == 4 and d.day <= 7).to_numpy()
    vol += np.where(first_fri & (hour == 15) & (minute == 30), 2500.0, 0.0)
    # ロンドン16時FIX: 通常期 18:00 / 英米DSTギャップ期間 19:00（Phase 0-2 の検証(D)用）
    uk = dates.map(tz.is_uk_dst).to_numpy()
    fix_hour = np.where(dst & ~uk, 19, 18)
    vol += np.where((hour == fix_hour) & (minute == 0), 1200.0, 0.0)

    # spread(points, 3桁想定): 早朝とNYクローズ前後で拡大
    base = np.where((hour >= 22) | (hour <= 1), 28.0, 0.0) + 11.0
    sp_pts = np.maximum(3.0, rng.normal(base, 2.0, idx.size)).round()

    return pd.DataFrame({
        "<DATE>": [d.strftime("%Y.%m.%d") for d in idx],
        "<TIME>": [d.strftime("%H:%M:%S") for d in idx],
        "<OPEN>": np.round(open_, 3), "<HIGH>": np.round(high, 3),
        "<LOW>": np.round(low, 3), "<CLOSE>": np.round(close, 3),
        "<TICKVOL>": vol.round().astype(int), "<VOL>": 0,
        "<SPREAD>": sp_pts.astype(int),
    })


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--end", default="2026-09-12 23:59")
    ap.add_argument("--out", default="usdjpy_research/data/SYNTHETIC_USDJPY_M1.csv")
    a = ap.parse_args()
    df = build(a.start, a.end)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, sep="\t", index=False)
    print(f"{SYNTHETIC_HEADER}\n{len(df):,} bars -> {out}")


if __name__ == "__main__":
    main()
