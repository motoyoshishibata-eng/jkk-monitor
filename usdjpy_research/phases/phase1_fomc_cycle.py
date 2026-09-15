"""Phase 1: FOMCサイクル時間。

事前登録（走らせる前に固定した内容。実行後に動かしてはいけない）:
  仮説   : 前回FOMCからの経過営業日で切ると USDJPY のリターンに偏りがある。
           株のリスクプレミアムが偶数週に集中する（Cieslak et al. 2019）のと
           同型の偏りが、リスクオン＝円売りを通じて USDJPY に乗る。
  観測窓 : day -1〜3 / 9〜13 / 19〜23 / 29〜33 を「偶数週＝ロング候補期間」と定義。
           day は営業日ベース、day 0 = FOMC 声明発表日。
  閾値   : 指示書 §1 共通基準（|t|>=2, PF>=1.3, n>=100, コスト控除後）。
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
from ..common.io import load_mt5_bars, PIP
from ..common.report import PhaseReport
from ..common.stats import summarize, bonferroni_t, TestCounter

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
        write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
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
    trades = simulate(df, _to_signals(_blocks(cycle, EVEN_WEEK_DAYS), exec_hour, exec_minute),
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
        rows = []
        for name, grp in trades.groupby(lab.to_numpy()):
            counter.count(f"偶数週ロング（{name}）")
            r = summarize(grp["net_pips"])
            rows.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
        tables.append(("円高期 / 円安期 別（コスト控除後）",
                       pd.DataFrame(rows, columns=["期", "n", "平均pips", "t", "PF"])
                       .to_string(index=False)))
        regime_str = " / ".join(f"{r[0]}: t={r[3]} PF={r[4]} n={r[1]}" for r in rows)
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

    # --- 判定 ---
    # 事前登録した検定回数と実際に走らせた回数の大きい方を採る
    # （データ期間の都合でレジーム別が減っても、補正を緩めないため）
    n_tests = max(N_TESTS, len(counter))
    crit = bonferroni_t(n_tests)
    chk = res.passes(n_tests)
    verdict = "合格" if (chk["overall"] and stable) else "不合格"
    if verdict == "不合格":
        if res.n < 100:
            reason = f"取引回数 {res.n} 回で基準の100回未満。統計的に判定不能。"
        elif abs(res.t) < 2:
            reason = (f"コスト控除後 t={res.t:.2f} で基準の|t|>=2 に届かない"
                      f"（控除前 t={gross.t:.2f}）。")
        elif res.pf < 1.3:
            reason = f"コスト控除後 PF={res.pf:.2f} で基準の1.3未満。"
        else:
            reason = "窓を±1営業日ずらすと符号が反転する（偶然の可能性が高い）。"
    else:
        reason = (f"コスト控除後 t={res.t:.2f} / PF={res.pf:.2f} / n={res.n} で共通基準を満たし、"
                  f"±1日の近傍でも符号が安定。"
                  + ("" if abs(res.t) >= crit else
                     f" ただし Bonferroni臨界 {crit:.2f} 未達なので要追試。"))

    rep = PhaseReport(
        phase="Phase 1", axis="FOMCサイクル時間",
        hypothesis="前回FOMCからの経過営業日（偶数週）に USDJPY のロング優位が乗る",
        preregistered=f"偶数週 = day -1〜3, 9〜13, 19〜23, 29〜33（営業日ベース, day0=声明発表日）"
                      f" / 約定サーバー{exec_hour:02d}:{exec_minute:02d} / SL {sl_pips:.0f}pips",
        n_tests=n_tests, bonferroni_crit_t=crit,
        result_after_cost=f"t = {res.t:.3f} / PF = {res.pf:.3f} / 取引回数 = {res.n}",
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct="サイクル日別リターン表（day別の偏りは他Phaseのフィルター候補になる）",
        tables=tables, notes=notes)
    if write:
        rep.write("phase1_fomc_cycle")
    return rep
