"""Phase 0-2: サーバー時間 / DST 前提の実データ検証。

指示書 §0-2 の検証タスクは「切替週の週足開始時刻が1時間ずれること」を
確認せよ、というものだが、**サーバーが GMT+3/+2 で米国DSTに追従している
なら、週の開始時刻(日曜17:00ET)はサーバー時間では年間 00:00 固定になり、
ずれない。** ずれないことこそが前提が正しい証拠になる。

そこで実際に前提を検証できるのは次の非対称性:

  (A) 米国イベント（雇用統計 08:30ET）はサーバー時間で年間固定のはず
      -> 夏冬どちらでも 15:30 にスパイクが出る
  (B) 東京イベント（仲値 09:55 JST）はサーバー時間で夏冬 1 時間ずれるはず
      -> 夏 03:55 / 冬 02:55 にスパイクが出る
  (C) 週の開始・終了時刻はサーバー時間で夏冬とも同じはず

(A)(B)(C) がすべて成立すれば指示書 §0-2 の前提は正しい。
どれかが崩れたら **定数を書き換えず、そのまま報告すること。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..common import tz
from ..common.io import load_mt5_bars, describe_bars
from ..common.report import PhaseReport


def _activity(df: pd.DataFrame) -> pd.Series:
    """バーの「活況度」。tickvol があれば優先、無ければ高安レンジ。"""
    if "tickvol" in df.columns and df["tickvol"].fillna(0).sum() > 0:
        return df["tickvol"].astype("float64")
    return (df["high"] - df["low"]).astype("float64")


def _minute_profile(df: pd.DataFrame, act: pd.Series, mask,
                    lo_min: int, hi_min: int) -> pd.Series:
    """サーバー時間の分刻みプロファイル（1日の通算分 lo..hi）。"""
    mins = df.index.hour * 60 + df.index.minute
    sel = mask & (mins >= lo_min) & (mins <= hi_min)
    if not sel.any():
        return pd.Series(dtype="float64")
    return act[sel].groupby(mins[sel]).mean()


def _hhmm(total_min: int) -> str:
    return f"{total_min // 60:02d}:{total_min % 60:02d}"


def run(bars_path: str, write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
    act = _activity(df)
    step = pd.Series(df.index).diff().mode()
    is_m1 = len(step) and step.iloc[0] <= pd.Timedelta(minutes=1)

    dates = pd.Series(df.index.date, index=df.index)
    dst = dates.map(tz.is_us_dst).to_numpy()
    std = ~dst

    notes, tables = [], []
    checks = {}

    # --- (C) 週の開始・終了時刻 ---
    wk = pd.DataFrame({"dow": df.index.dayofweek, "hour": df.index.hour,
                       "dst": dst}, index=df.index)
    first_bar = (wk.groupby([wk.index.isocalendar().year,
                             wk.index.isocalendar().week])
                   .head(1))
    last_bar = (wk.groupby([wk.index.isocalendar().year,
                            wk.index.isocalendar().week])
                  .tail(1))
    open_dst = first_bar.loc[first_bar["dst"], "hour"]
    open_std = first_bar.loc[~first_bar["dst"], "hour"]
    close_dst = last_bar.loc[last_bar["dst"], "hour"]
    close_std = last_bar.loc[~last_bar["dst"], "hour"]
    same_open = (len(open_dst) and len(open_std)
                 and open_dst.mode().iloc[0] == open_std.mode().iloc[0])
    checks["(C) 週開始がサーバー時間で夏冬同一"] = bool(same_open)
    tables.append(("(C) 週の開始・終了バー（サーバー時間の時）", "\n".join([
        f"夏時間週 開始hour 最頻値 = {open_dst.mode().iloc[0] if len(open_dst) else '?'}"
        f"  (n={len(open_dst)})",
        f"標準時週 開始hour 最頻値 = {open_std.mode().iloc[0] if len(open_std) else '?'}"
        f"  (n={len(open_std)})",
        f"夏時間週 終了hour 最頻値 = {close_dst.mode().iloc[0] if len(close_dst) else '?'}",
        f"標準時週 終了hour 最頻値 = {close_std.mode().iloc[0] if len(close_std) else '?'}",
        "",
        "期待: サーバーが GMT+3/+2 で米DSTに追従しているなら、週の開始は",
        "      日曜17:00ET = サーバー00:00 で年間固定（＝ずれないのが正常）。",
    ])))

    if is_m1:
        # --- (B) 東京仲値 09:55 JST ---
        p_dst = _minute_profile(df, act, dst, 2 * 60 + 30, 4 * 60 + 30)
        p_std = _minute_profile(df, act, std, 2 * 60 + 30, 4 * 60 + 30)
        peak_dst = int(p_dst.idxmax()) if len(p_dst) else -1
        peak_std = int(p_std.idxmax()) if len(p_std) else -1
        shift = peak_dst - peak_std
        checks["(B) 東京仲値ピークが夏冬で+60分ずれる"] = (shift == 60)
        tables.append(("(B) 東京仲値まわりの活況度ピーク（サーバー時間）", "\n".join([
            f"夏時間: ピーク {_hhmm(peak_dst)}  （期待 03:55）",
            f"標準時: ピーク {_hhmm(peak_std)}  （期待 02:55）",
            f"ずれ  : {shift:+d} 分（期待 +60）",
        ])))

        # --- (A) 米雇用統計 08:30ET = サーバー15:30 ---
        first_fri = pd.Series(df.index.date, index=df.index).map(
            lambda d: d.weekday() == 4 and d.day <= 7).to_numpy()
        a_dst = _minute_profile(df, act, dst & first_fri, 15 * 60, 16 * 60)
        a_std = _minute_profile(df, act, std & first_fri, 15 * 60, 16 * 60)
        nfp_dst = int(a_dst.idxmax()) if len(a_dst) else -1
        nfp_std = int(a_std.idxmax()) if len(a_std) else -1
        checks["(A) 雇用統計ピークが夏冬とも 15:30 で固定"] = (
            nfp_dst == nfp_std == 15 * 60 + 30)
        tables.append(("(A) 雇用統計日（第1金曜）のピーク（サーバー時間）", "\n".join([
            f"夏時間: ピーク {_hhmm(nfp_dst)}  （期待 15:30）",
            f"標準時: ピーク {_hhmm(nfp_std)}  （期待 15:30）",
            "ずれていなければ、米国イベント基準の Phase 1〜3 は DST 処理不要。",
        ])))
    else:
        notes.append("M1 データではないため (A)(B) の分単位検証をスキップした。"
                     " 必ず M1 を出力して再実行すること。")
        prof = pd.DataFrame({"hour": df.index.hour, "act": act.to_numpy(),
                             "dst": dst})
        piv = prof.pivot_table(index="hour", columns="dst", values="act",
                               aggfunc="mean")
        tables.append(("時間帯別 活況度（左=標準時 右=夏時間）", piv.to_string()))

    ok = all(checks.values())
    body = "\n".join(f"{'OK ' if v else 'NG '} {k}" for k, v in checks.items())
    tables.insert(0, ("検証結果サマリ", body))
    tables.append(("読み込んだデータ", describe_bars(df)))

    rep = PhaseReport(
        phase="Phase 0-2", axis="サーバー時間 / DST 前提の実データ検証",
        hypothesis="ファイネストMT5は GMT+3(米夏)/GMT+2(米冬) で米国DSTに追従する",
        preregistered="(A)雇用統計は 15:30 固定 / (B)東京仲値は 03:55⇔02:55 で60分ずれ /"
                      " (C)週開始時刻は夏冬同一",
        n_tests=len(checks),
        result_after_cost="該当なし（コストのかからないデータ検証）",
        neighborhood="該当なし",
        verdict="前提は成立" if ok else "前提が崩れている（要報告）",
        reason=("(A)(B)(C) すべて成立。米国イベント基準の Phase 1〜3 は DST 処理不要。"
                if ok else
                "上記 NG 項目があるため、指示書 §0-2 の前提をそのまま使えない。"
                "定数を書き換えず、まず報告すること。"),
        byproduct="時間帯別の活況度プロファイル",
        tables=tables, notes=notes)
    if write:
        rep.write("phase0_dst")
    return rep
