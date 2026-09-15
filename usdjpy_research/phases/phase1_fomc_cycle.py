"""Phase 1: FOMCサイクル時間。

事前登録（走らせる前に固定した内容。実行後に動かしてはいけない）:
  仮説   : 前回FOMCからの経過営業日で切ると USDJPY のリターンに偏りがある。
           株のリスクプレミアムが偶数週に集中する（Cieslak et al. 2019）のと
           同型の偏りが、リスクオン＝円売りを通じて USDJPY に乗る。
  観測窓 : day -1〜3 / 9〜13 / 19〜23 / 29〜33 を「偶数週＝ロング候補期間」と定義。
           day は営業日ベース、day 0 = FOMC 声明発表日。
  判定   : 事前登録された単一仮説のイベントスタディなので、
           **パーミュテーション検定**（10,000回・曜日月・保有日数を層化・片側2.5%以内）
           + PF>=1.3（コスト控除後）で判定する（v1.1 差分4）。
           n>=100 は総当たり探索用の安全装置なので、ここでは課さない。
  検定回数: 主検定1（全期間の偶数週ロング）+ レジーム別2 + 近傍±1日2 = 5。

禁止事項:
  結果を見てから「実は day 11〜15 が良かった」とラベルを動かすのは禁止。
  近傍安定性チェック(±1日)は符号が反転しないかを見るためだけに使い、
  良い方の窓を採用してはいけない。
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from ..common import events, regime
from ..common.backtest import simulate, series_at_hour
from ..common.cost import CostModel
from ..common.io import load_mt5_bars, resample_bars, PIP
from ..common.report import PhaseReport
from ..common.stats import (summarize, bonferroni_t, TestCounter, judge,
                            regime_cell, permutation_test_multi, PERM_ITERS)

# --- 事前登録した窓（実行後に変更禁止） ---
EVEN_WEEK_DAYS: frozenset[int] = frozenset(
    list(range(-1, 4)) + list(range(9, 14)) + list(range(19, 24)) + list(range(29, 34)))
N_TESTS = 5
DEFAULT_EXEC_HOUR = 17    # サーバー17:00。流動性が厚くスプレッドが薄い時間帯
DEFAULT_SL_PIPS = 120.0


def _blocks(cycle: pd.Series, days: frozenset[int]):
    """連続する該当日をひとかたまりの保有区間 (エントリー基準日, 決済基準日) にする。

    エントリーは区間初日の「前営業日」の約定時刻、決済は区間最終日の約定時刻。
    こうすると区間内の各営業日リターンがちょうど1回ずつ入る。
    """
    flag = cycle.isin(days).to_numpy()
    idx = cycle.index
    out, i = [], 0
    while i < len(flag):
        if not flag[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(flag) and flag[j + 1]:
            j += 1
        if i - 1 >= 0:
            out.append((idx[i - 1], idx[j]))
        i = j + 1
    return out


def _hold_len(idx: pd.DatetimeIndex, a, b) -> int:
    """エントリー基準日 a から決済基準日 b までの保有営業日数。"""
    return int(idx.get_loc(b) - idx.get_loc(a))


def _to_signals(blocks, hour: int, minute: int):
    for a, b in blocks:
        ta = _dt.datetime.combine(a.date(), _dt.time(hour, minute))
        tb = _dt.datetime.combine(b.date(), _dt.time(hour, minute))
        if tb > ta:
            yield (ta, tb, +1, f"{a.date()}->{b.date()}")


def _t_of(s: pd.Series) -> float:
    return (s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) > 1 else float("nan")


def run(bars_path: str, cost: CostModel, *, exec_hour: int = DEFAULT_EXEC_HOUR,
        exec_minute: int = 0, sl_pips: float = DEFAULT_SL_PIPS,
        n_iter: int = PERM_ITERS, write: bool = True) -> PhaseReport:
    # スイング保有なので1時間足で足りる。SL の到達判定は M1 と同じ答えになり、
    # パーミュテーション検定を1万回回しても現実的な時間で終わる。
    df = resample_bars(load_mt5_bars(bars_path), "1h")
    px = series_at_hour(df, exec_hour, exec_minute)
    tdays = events.trading_day_index(px.index)

    fomc = events.load_fomc(scheduled_only=True)
    _, audit_msgs = events.audit_fomc()
    cycle = events.fomc_cycle_day(tdays, pd.DatetimeIndex(fomc["date"])).dropna()

    counter = TestCounter()
    notes = [b for b in [regime.warning_banner().strip()] if b]
    notes += [f"FOMC日程チェック: {m}" for m in audit_msgs]
    notes.append(f"約定はサーバー時間 {exec_hour:02d}:{exec_minute:02d}、SL={sl_pips:.0f}pips。")
    notes.append("近傍チェックは符号の安定性を見るためのもの。"
                 "良い方の窓を採用したらカーブフィッティングです。")
    tables = []

    # --- (1) 記述統計（コスト控除前・参考値） ---
    day_ret = (px.reindex(tdays).diff() / PIP).dropna()
    desc = pd.DataFrame({"pips": day_ret,
                         "cycle_day": cycle.reindex(day_ret.index)}).dropna()
    by_day = desc.groupby("cycle_day")["pips"].agg(["count", "mean"])
    by_day["t"] = desc.groupby("cycle_day")["pips"].apply(_t_of)
    tables.append(("サイクル日別 日次リターン（pips, コスト控除前・参考値）",
                   by_day.round(3).to_string()))

    even = desc["cycle_day"].isin(EVEN_WEEK_DAYS)
    split = pd.DataFrame(
        {"n": [int(even.sum()), int((~even).sum())],
         "mean_pips": [desc.loc[even, "pips"].mean(), desc.loc[~even, "pips"].mean()],
         "t": [_t_of(desc.loc[even, "pips"]), _t_of(desc.loc[~even, "pips"])]},
        index=["偶数週", "奇数週"]).round(3)
    tables.append(("偶数週 / 奇数週（日次, コスト控除前・参考値）", split.to_string()))

    # --- (2) 主検定: 偶数週ロング（コスト控除後） ---
    blocks = _blocks(cycle, EVEN_WEEK_DAYS)
    trades = simulate(df, _to_signals(blocks, exec_hour, exec_minute),
                      cost, sl_pips=sl_pips)
    counter.count("偶数週ロング（全期間）")
    res = summarize(trades["net_pips"] if len(trades) else [])
    gross = summarize(trades["gross_pips"] if len(trades) else [])
    tables.append(("主検定: 偶数週ロング（コスト控除後が判定対象）", "\n".join([
        f"取引回数      : {res.n}",
        f"平均(控除後)  : {res.mean_pips:.2f} pips   / 控除前 {gross.mean_pips:.2f} pips",
        f"t値 (控除後)  : {res.t:.3f}             / 控除前 {gross.t:.3f}",
        f"PF  (控除後)  : {res.pf:.3f}             / 控除前 {gross.pf:.3f}",
        f"勝率          : {res.win_rate*100:.1f}%",
        f"総損益(控除後): {res.total_pips:.0f} pips",
        f"最大DD        : {res.max_dd_pips:.0f} pips",
        f"SL到達率      : {trades['hit_sl'].mean()*100:.1f}%" if len(trades) else "SL到達率: n/a",
    ])))

    # --- (3) レジーム別 ---
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        cells = []
        for name, grp in trades.groupby(lab.to_numpy()):
            counter.count(f"偶数週ロング（{name}）")
            cells.append(f"{name}: {regime_cell(summarize(grp['net_pips']))}")
        tables.append(("円高期 / 円安期 別（コスト控除後）", "\n".join(cells)
                       + "\n\nレジーム別は参考値であり、単独では合否判定に使わない"
                         "（v1.1 差分4）。n<30 の期は無理に数字を出さない。"))
        regime_str = " / ".join(cells)
    else:
        regime_str = "取引なし"

    # --- (4) 近傍安定性: 窓を ±1営業日ずらす ---
    stab = []
    for shift in (-1, +1):
        shifted = frozenset(d + shift for d in EVEN_WEEK_DAYS)
        tr = simulate(df, _to_signals(_blocks(cycle, shifted), exec_hour, exec_minute),
                      cost, sl_pips=sl_pips)
        counter.count(f"近傍 {shift:+d}日")
        r = summarize(tr["net_pips"] if len(tr) else [])
        stab.append([f"{shift:+d}日", r.n, round(r.mean_pips, 2), round(r.t, 3)])
    tables.append(("近傍安定性（窓を±1営業日ずらす）",
                   pd.DataFrame([["基準", res.n, round(res.mean_pips, 2), round(res.t, 3)]] + stab,
                                columns=["窓", "n", "平均pips", "t"]).to_string(index=False)))
    signs = {np.sign(v) for v in [res.mean_pips] + [s[2] for s in stab] if v == v}
    stable = len(signs) == 1

    # --- 差分4: パーミュテーション検定 ---
    # 偶数週ブロックの「入り口の日」をランダムな営業日に置き換える。
    # 保有営業日数が違うブロックを混ぜると帰無分布が別物になるので、
    # 保有日数もグループとして層化する。
    lengths = sorted({_hold_len(cycle.index, a, b) for a, b in blocks})
    contrib_by_group, cand_days = {}, list(cycle.index)
    for L in lengths:
        sigs = []
        for i, day in enumerate(cand_days):
            if i + L >= len(cand_days):
                continue
            t0 = _dt.datetime.combine(day.date(), _dt.time(exec_hour, exec_minute))
            t1 = _dt.datetime.combine(cand_days[i + L].date(),
                                      _dt.time(exec_hour, exec_minute))
            sigs.append((t0, t1, +1, str(day.date())))
        tr = simulate(df, sigs, cost, sl_pips=sl_pips)
        if len(tr):
            c = tr.groupby("tag")["net_pips"].sum()
            c.index = pd.DatetimeIndex(c.index)
            contrib_by_group[L] = c.sort_index()
    ev_multi = [(a, _hold_len(cycle.index, a, b)) for a, b in blocks]
    perm = permutation_test_multi(contrib_by_group, ev_multi, n_iter=n_iter)
    tables.append((f"パーミュテーション検定（v1.1 差分4・{n_iter:,}回）",
                   perm.summary() + "\n\n"
                   "偶数週ブロックの入り口の日をランダムな営業日に置き換え、"
                   "曜日・月・保有営業日数を実測に合わせて層化抽出している。\n"
                   f"保有日数の型: {lengths}"
                   + (f"\n注意: {perm.strata_note}" if perm.strata_note else "")))

    # --- 判定 ---
    n_tests = max(N_TESTS, len(counter))
    crit = bonferroni_t(n_tests)
    ok, reason = judge(res, kind="event", perm=perm)
    if ok and not stable:
        ok, reason = False, "窓を±1営業日ずらすと符号が反転する（偶然の可能性が高い）。"
    verdict = "合格" if ok else "不合格"
    reason += f"（控除前 t={gross.t:.2f} / 控除後 t={res.t:.2f}）"

    rep = PhaseReport(
        phase="Phase 1", axis="FOMCサイクル時間",
        hypothesis="前回FOMCからの経過営業日（偶数週）に USDJPY のロング優位が乗る",
        preregistered=f"偶数週 = day -1〜3, 9〜13, 19〜23, 29〜33（営業日ベース, day0=声明発表日）"
                      f" / 約定サーバー{exec_hour:02d}:{exec_minute:02d} / SL {sl_pips:.0f}pips",
        n_tests=n_tests, bonferroni_crit_t=crit,
        judgment_method=f"パーミュテーション検定 {n_iter:,}回"
                        "（曜日・月・保有日数を層化）+ PF>=1.3",
        permutation=perm.summary().replace("\n", " / "),
        result_after_cost=f"片側p = {perm.p_one_sided:.4f}"
                          f"（{perm.percentile:.1f}パーセンタイル）/ "
                          f"PF = {res.pf:.3f} / 取引回数 = {res.n} / t = {res.t:.3f}",
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct="サイクル日別リターン表（day別の偏りは他Phaseのフィルター候補になる）",
        tables=tables, notes=notes)
    if write:
        rep.write("phase1_fomc_cycle")
    return rep
