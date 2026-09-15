"""シグナル -> 取引 -> コスト控除後PnL の共通シミュレータ。

Phase 1〜6 はどれも「(エントリー時刻, 決済時刻, 方向) のリスト」に落ちるので、
約定・SL・コストの扱いはここに一本化する。各 Phase で書き分けると、
Phase ごとにコスト前提が微妙に違う、という一番まずい事故が起きるため。

前提:
  - MT5 のバーは bid。買いは entry で spread 分不利になるので、
    往復スプレッドは cost.CostModel が1回分だけ計上する。
  - SL は **必ず設定する**（指示書 §0 の絶対条件）。sl_pips=None は
    明示的に許可フラグを立てない限り例外にする。
  - SL ヒット時の約定は「SL価格ぴったり」ではなく
    sl_slippage_pips だけ不利側にずらす（指示書 §4 の悲観シナリオ）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .cost import CostModel
from .io import PIP


@dataclass
class Trade:
    entry_time: _dt.datetime
    exit_time: _dt.datetime
    direction: int          # +1 = 買い, -1 = 売り
    entry_price: float
    exit_price: float
    gross_pips: float
    net_pips: float
    hit_sl: bool
    tag: str = ""


def price_at_or_after(df: pd.DataFrame, when: _dt.datetime,
                      max_gap: _dt.timedelta = _dt.timedelta(hours=6)):
    """指定時刻以降の最初のバーの open を約定価格として返す。

    週末・祝日で穴が空いている場合、max_gap を超えたら None（=取引不成立）。
    穴を無視して遠い価格で約定させると、バックテストが実運用より楽観的になる。
    """
    pos = df.index.searchsorted(pd.Timestamp(when), side="left")
    if pos >= len(df.index):
        return None, None
    ts = df.index[pos]
    if ts - pd.Timestamp(when) > max_gap:
        return None, None
    return ts, float(df["open"].iloc[pos])


def simulate(df: pd.DataFrame, signals, cost: CostModel, *,
             sl_pips: float, tp_pips: float | None = None,
             sl_slippage_pips: float = 0.0,
             allow_no_sl: bool = False) -> pd.DataFrame:
    """signals = iterable of (entry_dt, exit_dt, direction, tag)。

    返り値は Trade を並べた DataFrame。net_pips がコスト控除後の損益。
    """
    if sl_pips is None and not allow_no_sl:
        raise ValueError(
            "sl_pips が未設定。指示書は SL 必須（含み損放置の禁止）なので、"
            "意図的に SL 無しを見たい場合のみ allow_no_sl=True を渡すこと。")

    idx = df.index
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    rows: list[Trade] = []

    for entry_dt, exit_dt, direction, tag in signals:
        e_ts, e_px = price_at_or_after(df, entry_dt)
        if e_ts is None or e_ts >= pd.Timestamp(exit_dt):
            continue
        # 買いは ask で入るが、コストは cost 側で1回分まとめて引くので
        # ここでは bid ベースの価格をそのまま使う。
        i0 = idx.searchsorted(e_ts, side="right")
        i1 = idx.searchsorted(pd.Timestamp(exit_dt), side="right")
        hit_sl = False
        x_ts, x_px = None, None

        if i1 > i0 and sl_pips is not None:
            seg_hi, seg_lo = highs[i0:i1], lows[i0:i1]
            if direction > 0:
                sl_px = e_px - sl_pips * PIP
                bad = np.flatnonzero(seg_lo <= sl_px)
                good = (np.flatnonzero(seg_hi >= e_px + tp_pips * PIP)
                        if tp_pips else np.array([], dtype=int))
            else:
                sl_px = e_px + sl_pips * PIP
                bad = np.flatnonzero(seg_hi >= sl_px)
                good = (np.flatnonzero(seg_lo <= e_px - tp_pips * PIP)
                        if tp_pips else np.array([], dtype=int))
            first_bad = bad[0] if bad.size else None
            first_good = good[0] if good.size else None
            if first_bad is not None and (first_good is None or first_bad <= first_good):
                hit_sl = True
                x_ts = idx[i0 + first_bad]
                # SL は不利側にスリップさせる（悲観シナリオ）
                x_px = sl_px - direction * sl_slippage_pips * PIP
            elif first_good is not None:
                x_ts = idx[i0 + first_good]
                x_px = e_px + direction * tp_pips * PIP

        if x_ts is None:
            x_ts, x_px = price_at_or_after(df, exit_dt)
            if x_ts is None:
                pos = idx.searchsorted(pd.Timestamp(exit_dt), side="right") - 1
                if pos <= i0:
                    continue
                x_ts, x_px = idx[pos], float(df["close"].iloc[pos])

        gross = direction * (x_px - e_px) / PIP
        net = cost.pnl_after_cost(gross, e_ts.to_pydatetime(),
                                  x_ts.to_pydatetime(), direction)
        rows.append(Trade(e_ts.to_pydatetime(), x_ts.to_pydatetime(), direction,
                          e_px, x_px, gross, net, hit_sl, tag))

    if not rows:
        return pd.DataFrame(columns=[f.name for f in Trade.__dataclass_fields__.values()]
                            if False else
                            ["entry_time", "exit_time", "direction", "entry_price",
                             "exit_price", "gross_pips", "net_pips", "hit_sl", "tag"])
    out = pd.DataFrame([t.__dict__ for t in rows])
    return out.sort_values("entry_time").reset_index(drop=True)


def series_at_hour(df: pd.DataFrame, hour: int, minute: int = 0) -> pd.Series:
    """各営業日のサーバー時間 hour:minute の価格（その時刻以降の最初のバーの open）。

    日足close(=サーバー0時)ではなく取引時刻の価格を使うことで、
    バックテストの約定とコストの前提を揃える。
    """
    want = (df.index.hour == hour) & (df.index.minute == minute)
    if want.any():
        s = df.loc[want, "open"]
    else:  # 時間足など minute 粒度が無い場合
        s = df.loc[df.index.hour == hour, "open"]
    s = s[~pd.Series(s.index.date, index=s.index).duplicated(keep="first")]
    s.index = pd.DatetimeIndex(s.index)
    return s


def to_pips(s: pd.Series) -> pd.Series:
    """価格系列 -> 前営業日比の pips 変化。"""
    return (s.diff() / PIP).dropna()


def simulate_stop_entry(df: pd.DataFrame, setups, cost: CostModel, *,
                        sl_pips: float, entry_slippage_pips: float = 0.0,
                        sl_slippage_pips: float = 0.0) -> pd.DataFrame:
    """逆指値（ブレイクアウト）エントリー版。

    setups = iterable of (window_start, window_end, direction, trigger_price,
                          exit_dt, tag)
    window 内で trigger_price に触れたら約定したとみなす。約定価格は
    trigger_price を entry_slippage_pips だけ不利側にずらした値
    （逆指値は不利に滑るのが普通なので、有利側に約定させない）。

    触れなければその日は取引なし（成立しなかったことも情報なので、
    呼び出し側で「セットアップ数」と「成立数」を両方記録すること）。
    """
    if sl_pips is None:
        raise ValueError("sl_pips が未設定。指示書は SL 必須。")
    idx = df.index
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    rows: list[Trade] = []

    for w0, w1, direction, trigger, exit_dt, tag in setups:
        i0 = idx.searchsorted(pd.Timestamp(w0), side="left")
        i1 = idx.searchsorted(pd.Timestamp(w1), side="right")
        if i1 <= i0:
            continue
        seg_hi, seg_lo = highs[i0:i1], lows[i0:i1]
        touch = (np.flatnonzero(seg_hi >= trigger) if direction > 0
                 else np.flatnonzero(seg_lo <= trigger))
        if not touch.size:
            continue
        k = int(touch[0])
        e_ts = idx[i0 + k]
        e_px = trigger + direction * entry_slippage_pips * PIP

        j0 = i0 + k + 1
        j1 = idx.searchsorted(pd.Timestamp(exit_dt), side="right")
        hit_sl = False
        x_ts = x_px = None
        if j1 > j0:
            sl_px = e_px - direction * sl_pips * PIP
            bad = (np.flatnonzero(lows[j0:j1] <= sl_px) if direction > 0
                   else np.flatnonzero(highs[j0:j1] >= sl_px))
            if bad.size:
                hit_sl = True
                x_ts = idx[j0 + int(bad[0])]
                x_px = sl_px - direction * sl_slippage_pips * PIP
        if x_ts is None:
            pos = min(max(j1 - 1, j0), len(idx) - 1)
            if pos <= i0 + k:
                continue
            x_ts, x_px = idx[pos], float(df["close"].iloc[pos])

        gross = direction * (x_px - e_px) / PIP
        net = cost.pnl_after_cost(gross, e_ts.to_pydatetime(),
                                  x_ts.to_pydatetime(), direction)
        rows.append(Trade(e_ts.to_pydatetime(), x_ts.to_pydatetime(), direction,
                          e_px, x_px, gross, net, hit_sl, tag))

    cols = ["entry_time", "exit_time", "direction", "entry_price", "exit_price",
            "gross_pips", "net_pips", "hit_sl", "tag"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([t.__dict__ for t in rows]).sort_values("entry_time").reset_index(drop=True)
