"""Phase 2: プレFOMCドリフト（発表「前」24時間）。

事前登録:
  仮説   : FOMC発表前24時間に USDJPY が一方向に偏る。
           方向は事前に決め打ちできない（為替では見解が割れている）ため、
           **ロング方向で集計し両側t検定で判定する**。
           ショートが有意なら t が負で出る。走らせてから方向を選ぶことはしない。
  観測窓 : 前営業日 21:00（サーバー時間）→ 当日 20:55（サーバー時間）。
           FOMCはサーバー21:00固定なので DST 処理は不要（Phase 0-2 で検証済み）。
           内訳は3分割: 21:00→翌02:00 / 02:00→09:00 / 09:00→20:55。
  閾値   : 指示書 §1 共通基準（コスト控除後 |t|>=2, PF>=1.3, n>=100）。
  検定回数: 全窓1 + 内訳3 + レジーム別2 = 6。

安全上の制約（指示書 §4 の警告）:
  - 21:00 の発表をまたぐ保有は設計から外す。必ず 20:55 に決済する。
  - SL は広め。SL到達時は「ぴったり約定」ではなくスリッページを加算した
    悲観シナリオでも集計する（0 / 3 / 5 pips の3本を必ず併記）。
  - 日銀会合と近接する週は別掲する。

注意: 定例FOMCは年8回 = 20年で約160回しかない。全期間を使っても
      取引回数は 160 前後にしかならず、レジーム別に割ると 100 回を割る。
      その場合「n不足で判定不能」と書くこと。基準を下げてはいけない。
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from ..common import events, regime
from ..common.backtest import simulate
from ..common.cost import CostModel
from ..common.io import load_mt5_bars
from ..common.report import PhaseReport
from ..common.stats import summarize, bonferroni_t, TestCounter

# --- 事前登録した窓（サーバー時間、実行後に変更禁止） ---
FULL_WINDOW = ("前営業日21:00→当日20:55", (21, 0), (20, 55))
SUB_WINDOWS = [
    ("前営業日21:00→当日02:00（NY後半〜オセアニア）", (21, 0), (2, 0)),
    ("当日02:00→09:00（東京）", (2, 0), (9, 0)),
    ("当日09:00→20:55（ロンドン〜NY前半）", (9, 0), (20, 55)),
]
N_TESTS = 6
DEFAULT_SL_PIPS = 80.0            # 通常戦略より広め（指示書 §4）
SLIPPAGE_SCENARIOS = (0.0, 3.0, 5.0)
BOJ_PROXIMITY_DAYS = 3


def _prev_trading_day(tdays: pd.DatetimeIndex, d: pd.Timestamp):
    pos = tdays.searchsorted(d.normalize(), side="left") - 1
    return tdays[pos] if pos >= 0 else None


def _signals(fomc_dates, tdays, start_hm, end_hm, tags=None):
    """窓 (start_hm, end_hm) の売買シグナルを作る。

    start の時刻が end より遅い場合は「前営業日から当日へ」の跨ぎとみなす。
    FULL_WINDOW と最初の SUB_WINDOW がこれに当たる。
    """
    for d in fomc_dates:
        d = pd.Timestamp(d).normalize()
        cross_day = start_hm > end_hm
        base = _prev_trading_day(tdays, d) if cross_day else d
        if base is None:
            continue
        t0 = _dt.datetime.combine(base.date(), _dt.time(*start_hm))
        # 跨ぎ窓でも end が 02:00 のように翌日側なら当日日付、
        # 20:55 のように当日側でも当日日付。どちらも d で正しい。
        t1 = _dt.datetime.combine(d.date(), _dt.time(*end_hm))
        if t1 <= t0:
            continue
        yield (t0, t1, +1, str(d.date()))


def _fmt(r) -> str:
    return (f"n={r.n:>4}  平均={r.mean_pips:>7.2f}pips  t={r.t:>6.3f}  "
            f"PF={r.pf:>6.3f}  勝率={r.win_rate*100:>5.1f}%")


def run(bars_path: str, cost: CostModel, *, sl_pips: float = DEFAULT_SL_PIPS,
        write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
    tdays = events.trading_day_index(df.index)
    fomc = events.load_fomc(scheduled_only=True)
    fomc = fomc[(fomc["date"] >= tdays.min()) & (fomc["date"] <= tdays.max())]
    _, audit_msgs = events.audit_fomc()
    boj = events.load_boj()

    counter = TestCounter()
    tables, notes = [], [b for b in [regime.warning_banner().strip()] if b]
    notes += [f"FOMC日程チェック: {m}" for m in audit_msgs]
    notes.append(f"SL={sl_pips:.0f}pips（通常より広め）。21:00 の発表は必ず跨がない設計。")

    # --- 主検定: 24時間窓。スリッページ3シナリオを併記 ---
    label, s_hm, e_hm = FULL_WINDOW
    sig = list(_signals(fomc["date"], tdays, s_hm, e_hm))
    by_slip = {}
    for slip in SLIPPAGE_SCENARIOS:
        tr = simulate(df, sig, cost, sl_pips=sl_pips, sl_slippage_pips=slip)
        by_slip[slip] = (tr, summarize(tr["net_pips"] if len(tr) else []))
        counter.count(f"24時間窓 slip={slip}") if slip == 0 else None
    trades, res = by_slip[SLIPPAGE_SCENARIOS[0]]
    gross = summarize(trades["gross_pips"] if len(trades) else [])
    worst = by_slip[SLIPPAGE_SCENARIOS[-1]][1]

    tables.append((f"主検定: {label}（ロング方向・両側t検定）", "\n".join(
        [f"控除前         : {_fmt(gross)}"]
        + [f"控除後 slip{int(s)}pips: {_fmt(by_slip[s][1])}" for s in SLIPPAGE_SCENARIOS]
        + [f"SL到達率       : {trades['hit_sl'].mean()*100:.1f}%" if len(trades) else "",
           "",
           "判定は『控除後 slip0』を主、『控除後 slip5』を悲観シナリオとして見る。",
           "悲観シナリオで基準割れするなら実運用では通らないと考えること。"])))

    # --- 内訳3分割 ---
    rows = []
    for lab, a, b in SUB_WINDOWS:
        s = list(_signals(fomc["date"], tdays, a, b))
        tr = simulate(df, s, cost, sl_pips=sl_pips, sl_slippage_pips=3.0)
        counter.count(f"内訳 {lab}")
        r = summarize(tr["net_pips"] if len(tr) else [])
        rows.append([lab, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
    tables.append(("内訳（コスト控除後・slip3pips）",
                   pd.DataFrame(rows, columns=["窓", "n", "平均pips", "t", "PF"])
                   .to_string(index=False)))
    notes.append("内訳はどこで効いているかを見るための分解であり、"
                 "最も良い内訳だけを取り出して戦略にすると多重比較の罠に落ちる。")

    # --- レジーム別 ---
    regime_str = "取引なし"
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        rr = []
        for name, grp in trades.groupby(lab.to_numpy()):
            counter.count(f"レジーム {name}")
            r = summarize(grp["net_pips"])
            rr.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3),
                       "n不足" if r.n < 100 else ""])
        tables.append(("円高期 / 円安期 別（コスト控除後・slip0）",
                       pd.DataFrame(rr, columns=["期", "n", "平均pips", "t", "PF", "備考"])
                       .to_string(index=False)))
        regime_str = " / ".join(f"{r[0]}: t={r[3]} PF={r[4]} n={r[1]}" for r in rr)

    # --- 日銀会合との近接 ---
    if boj is not None and len(trades):
        bd = pd.DatetimeIndex(boj["date"]).normalize()
        ent = pd.DatetimeIndex(trades["entry_time"]).normalize()
        near = np.array([bool(((bd - e).days.map(abs) <= BOJ_PROXIMITY_DAYS).any())
                         for e in ent])
        rr = []
        for name, m in (("日銀会合が近接", near), ("近接なし", ~near)):
            r = summarize(trades.loc[m, "net_pips"])
            rr.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
        tables.append((f"日銀会合との近接（±{BOJ_PROXIMITY_DAYS}日）別",
                       pd.DataFrame(rr, columns=["区分", "n", "平均pips", "t", "PF"])
                       .to_string(index=False)))
    else:
        notes.append("日銀会合日程 config/boj_dates.csv が無いため、"
                     "円側イベント混入の別掲をスキップした。"
                     "日銀サイトの決定会合日程を入れて再実行すること。")

    # --- 近傍安定性: 窓を ±1時間ずらす ---
    stab = []
    for shift in (-1, +1):
        s = list(_signals(fomc["date"], tdays, (21 + shift, 0), (20 + shift, 55)))
        tr = simulate(df, s, cost, sl_pips=sl_pips)
        r = summarize(tr["net_pips"] if len(tr) else [])
        stab.append([f"{shift:+d}時間", r.n, round(r.mean_pips, 2), round(r.t, 3)])
    tables.append(("近傍安定性（窓を±1時間ずらす）",
                   pd.DataFrame([["基準", res.n, round(res.mean_pips, 2), round(res.t, 3)]] + stab,
                                columns=["窓", "n", "平均pips", "t"]).to_string(index=False)))
    signs = {np.sign(v) for v in [res.mean_pips] + [s[2] for s in stab] if v == v}
    stable = len(signs) == 1

    n_tests = max(N_TESTS, len(counter))
    crit = bonferroni_t(n_tests)
    chk = res.passes(n_tests)
    pessimistic_ok = abs(worst.t) >= 2 and worst.pf >= 1.3
    verdict = "合格" if (chk["overall"] and stable and pessimistic_ok) else "不合格"
    if verdict == "不合格":
        if res.n < 100:
            reason = (f"定例FOMCは年8回しかなく取引回数 {res.n} 回。"
                      f"基準の100回に届かず統計的に判定不能。")
        elif abs(res.t) < 2:
            reason = f"コスト控除後 t={res.t:.2f}（控除前 {gross.t:.2f}）で |t|>=2 に届かない。"
        elif res.pf < 1.3:
            reason = f"コスト控除後 PF={res.pf:.2f} で基準の1.3未満。"
        elif not pessimistic_ok:
            reason = (f"slip0 では基準を満たすが、slip5pips の悲観シナリオで "
                      f"t={worst.t:.2f}/PF={worst.pf:.2f} に落ちる。実運用では通らない。")
        else:
            reason = "窓を±1時間ずらすと符号が反転する。"
    else:
        reason = (f"控除後 t={res.t:.2f}/PF={res.pf:.2f}/n={res.n}、"
                  f"slip5pips の悲観シナリオでも t={worst.t:.2f} を維持。")

    rep = PhaseReport(
        phase="Phase 2", axis="プレFOMCドリフト（発表前24時間）",
        hypothesis="FOMC発表前24時間に USDJPY が一方向に偏る（方向は決め打ちせず両側検定）",
        preregistered="前営業日21:00→当日20:55（サーバー時間, FOMCは21:00固定）/"
                      " 内訳3分割 / SL80pips / スリッページ 0・3・5pips 併記",
        n_tests=n_tests, bonferroni_crit_t=crit,
        result_after_cost=f"t = {res.t:.3f} / PF = {res.pf:.3f} / 取引回数 = {res.n}"
                          f"（slip5悲観: t = {worst.t:.3f} / PF = {worst.pf:.3f}）",
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct="FOMC前24時間の時間帯別リターン分解",
        tables=tables, notes=notes)
    if write:
        rep.write("phase2_pre_fomc")
    return rep
