"""Phase 6: 四半期末のドル調達需給。

事前登録:
  仮説   : 四半期末・年末は規制上のバランスシート制約からドル調達コストが
           跳ね上がり、その需給がスポットに波及する。
  観測窓 : 四半期末（3・6・9・12月の最終営業日）を day 0 とし、営業日で ±5 日。
  方向   : 決め打ちしない（両側t検定）。
  閾値   : 指示書 §1 共通基準（コスト控除後）。
  検定回数: day別 11 + 集約1 + 年末別掲2 + 2015年前後2 = 16。

打ち切り判断（指示書 §8）:
  日付ダミーで何も出なければ、ベーシスの有料データを買ってまで深追いしない。
  このPhaseは早めに打ち切ること。

構造的な制約（先に書いておく）:
  四半期末は年4回しかないので、2007年起点でも取引回数は最大80回程度。
  指示書の「100回以上」を **原理的に満たせない**。day別の日次集計
  （n = 80四半期 × 11日 ≈ 880）は基準を満たすが、売買単位での判定は
  n不足になる。この場合に基準を下げるのではなく「n不足で判定不能」と
  書くこと。月末も含めれば n は増えるが、それは Phase 5 の領域で、
  ここで勝手に窓を広げると事前登録の意味がなくなる。
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from ..common import regime
from ..common.backtest import simulate, series_at_hour
from ..common.cost import CostModel
from ..common.io import load_mt5_bars, PIP
from ..common.report import PhaseReport
from ..common.stats import summarize, bonferroni_t, TestCounter

OFFSETS = list(range(-5, 6))
AGG_WINDOW = (-3, 0)      # 需給圧力が積み上がる区間として事前登録
N_TESTS = 16
DEFAULT_EXEC_HOUR = 17
DEFAULT_SL_PIPS = 100.0
STRUCTURE_BREAK_YEAR = 2015   # 日銀レビューが指摘する構造変化


def _quarter_end_days(tdays: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(tdays, index=tdays)
    q = s.groupby([tdays.year, tdays.quarter]).max()
    return pd.DatetimeIndex(sorted(q.values))


def _offset_map(tdays: pd.DatetimeIndex, anchors: pd.DatetimeIndex) -> pd.Series:
    """各営業日に「直近の四半期末からの営業日オフセット」を付ける（|off|<=5 のみ）。"""
    out = pd.Series(np.nan, index=tdays)
    pos = {d: i for i, d in enumerate(tdays)}
    for a in anchors:
        i = pos.get(a)
        if i is None:
            continue
        for off in OFFSETS:
            j = i + off
            if 0 <= j < len(tdays):
                out.iloc[j] = off
    return out


def _t_of(s: pd.Series) -> float:
    return (s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))) if len(s) > 1 else float("nan")


def run(bars_path: str, cost: CostModel, *, exec_hour: int = DEFAULT_EXEC_HOUR,
        sl_pips: float = DEFAULT_SL_PIPS, write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
    px = series_at_hour(df, exec_hour)
    tdays = pd.DatetimeIndex(sorted(set(px.index.normalize())))
    px.index = px.index.normalize()
    px = px[~px.index.duplicated(keep="first")]

    anchors = _quarter_end_days(tdays)
    off = _offset_map(tdays, anchors)
    ret = (px.reindex(tdays).diff() / PIP)

    counter = TestCounter()
    tables, notes = [], [b for b in [regime.warning_banner().strip()] if b]
    notes.append(f"約定はサーバー時間 {exec_hour:02d}:00。day0 = 四半期最終営業日。")
    notes.append("ベーシスの時系列は有料が多いため、まず日付ダミーのみで検証している"
                 "（指示書 §8 の方針）。ここで何も出なければ深追いしない。")

    # --- (1) day別プロファイル ---
    dat = pd.DataFrame({"pips": ret, "off": off,
                        "year": tdays.year, "month": tdays.month}).dropna(subset=["off"])
    dat = dat.dropna(subset=["pips"])
    prof = dat.groupby("off")["pips"].agg(["count", "mean", "std"])
    prof["t"] = dat.groupby("off")["pips"].apply(_t_of)
    prof["絶対値平均"] = dat.groupby("off")["pips"].apply(lambda s: s.abs().mean())
    for o in OFFSETS:
        counter.count(f"day{o:+d}")
    tables.append(("四半期末 day別 日次リターン（pips, コスト控除前・参考値）",
                   prof.round(3).to_string()))
    notes.append("day別の表は記述統計（コスト控除前）。合否判定は下の売買集計のみで行う。")

    # --- (2) 集約窓での売買（コスト控除後） ---
    a, b = AGG_WINDOW
    sigs = []
    pos = {d: i for i, d in enumerate(tdays)}
    for anc in anchors:
        i = pos.get(anc)
        if i is None or i + a - 1 < 0 or i + b >= len(tdays):
            continue
        t0 = _dt.datetime.combine(tdays[i + a - 1].date(), _dt.time(exec_hour, 0))
        t1 = _dt.datetime.combine(tdays[i + b].date(), _dt.time(exec_hour, 0))
        sigs.append((t0, t1, +1, str(anc.date())))
    trades = simulate(df, sigs, cost, sl_pips=sl_pips)
    counter.count(f"集約窓 day{a:+d}〜{b:+d}")
    res = summarize(trades["net_pips"] if len(trades) else [])
    gross = summarize(trades["gross_pips"] if len(trades) else [])
    tables.append((f"集約窓 day{a:+d}〜{b:+d} のロング（コスト控除後が判定対象）", "\n".join([
        f"取引回数     : {res.n}" + ("   <- 100回未満。四半期末は年4回しかないため構造的な限界。"
                                     if res.n < 100 else ""),
        f"平均(控除後) : {res.mean_pips:.2f} pips  / 控除前 {gross.mean_pips:.2f} pips",
        f"t値 (控除後) : {res.t:.3f}            / 控除前 {gross.t:.3f}",
        f"PF  (控除後) : {res.pf:.3f}            / 控除前 {gross.pf:.3f}",
        f"勝率         : {res.win_rate*100:.1f}%",
    ])))

    # --- (3) 年末（12月末）別掲 ---
    is_dec = dat["month"] == 12
    rows = []
    for name, m in (("12月末（年末）", is_dec), ("3/6/9月末", ~is_dec)):
        counter.count(f"年末別掲 {name}")
        sub = dat.loc[m & dat["off"].between(a, b), "pips"]
        rows.append([name, len(sub), round(sub.mean(), 3), round(_t_of(sub), 3)])
    tables.append((f"年末 vs その他四半期末（day{a:+d}〜{b:+d} の日次, 控除前）",
                   pd.DataFrame(rows, columns=["区分", "n(日)", "平均pips", "t"])
                   .to_string(index=False)))

    # --- (4) 2015年前後 ---
    rows = []
    for name, m in ((f"〜{STRUCTURE_BREAK_YEAR-1}年", dat["year"] < STRUCTURE_BREAK_YEAR),
                    (f"{STRUCTURE_BREAK_YEAR}年〜", dat["year"] >= STRUCTURE_BREAK_YEAR)):
        counter.count(f"2015年前後 {name}")
        sub = dat.loc[m & dat["off"].between(a, b), "pips"]
        vol = dat.loc[m & dat["off"].between(a, b), "pips"].abs().mean()
        rows.append([name, len(sub), round(sub.mean(), 3), round(_t_of(sub), 3),
                     round(vol, 3) if vol == vol else np.nan])
    tables.append((f"{STRUCTURE_BREAK_YEAR}年前後の分割（日銀レビューが指摘する構造変化）",
                   pd.DataFrame(rows, columns=["期間", "n(日)", "平均pips", "t", "絶対値平均"])
                   .to_string(index=False)))

    # --- (5) レジーム別 ---
    regime_str = "取引なし"
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        rows = []
        for name, grp in trades.groupby(lab.to_numpy()):
            r = summarize(grp["net_pips"])
            rows.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
        tables.append(("円高期 / 円安期 別（コスト控除後）",
                       pd.DataFrame(rows, columns=["期", "n", "平均pips", "t", "PF"])
                       .to_string(index=False)))
        regime_str = " / ".join(f"{r[0]}: t={r[3]} PF={r[4]} n={r[1]}" for r in rows)

    # --- 近傍安定性: 集約窓を ±1日ずらす ---
    stab = []
    for shift in (-1, +1):
        s2 = []
        for anc in anchors:
            i = pos.get(anc)
            if i is None or i + a + shift - 1 < 0 or i + b + shift >= len(tdays):
                continue
            s2.append((_dt.datetime.combine(tdays[i + a + shift - 1].date(), _dt.time(exec_hour, 0)),
                       _dt.datetime.combine(tdays[i + b + shift].date(), _dt.time(exec_hour, 0)),
                       +1, str(anc.date())))
        tr = simulate(df, s2, cost, sl_pips=sl_pips)
        r = summarize(tr["net_pips"] if len(tr) else [])
        stab.append([f"{shift:+d}日", r.n, round(r.mean_pips, 2), round(r.t, 3)])
    tables.append(("近傍安定性（集約窓を±1営業日ずらす）",
                   pd.DataFrame([["基準", res.n, round(res.mean_pips, 2), round(res.t, 3)]] + stab,
                                columns=["窓", "n", "平均pips", "t"]).to_string(index=False)))
    signs = {np.sign(v) for v in [res.mean_pips] + [s[2] for s in stab] if v == v}
    stable = len(signs) == 1

    n_tests = max(N_TESTS, len(counter))
    crit = bonferroni_t(n_tests)
    chk = res.passes(n_tests)
    verdict = "合格" if (chk["overall"] and stable) else "不合格"
    best_day = prof["t"].abs().idxmax() if len(prof) else None
    if verdict == "不合格":
        if res.n < 100:
            reason = (f"取引回数 {res.n} 回。四半期末は年4回しかなく、2007年起点でも"
                      f"100回に届かない構造的な限界のため判定不能。")
        elif abs(res.t) < 2:
            reason = f"コスト控除後 t={res.t:.2f} で |t|>=2 未満。日付ダミーでは何も出ない。"
        elif res.pf < 1.3:
            reason = f"コスト控除後 PF={res.pf:.2f} で基準の1.3未満。"
        else:
            reason = "集約窓を±1営業日ずらすと符号が反転する。"
    else:
        reason = f"控除後 t={res.t:.2f}/PF={res.pf:.2f}/n={res.n} で共通基準を満たす。"

    rep = PhaseReport(
        phase="Phase 6", axis="四半期末のドル調達需給",
        hypothesis="四半期末・年末のドル調達需給がスポットに波及する",
        preregistered=f"四半期最終営業日を day0 とし営業日 ±5 / 集約窓 day{a:+d}〜{b:+d} /"
                      f" 両側検定 / 約定サーバー{exec_hour:02d}:00 / SL {sl_pips:.0f}pips",
        n_tests=n_tests, bonferroni_crit_t=crit,
        result_after_cost=f"t = {res.t:.3f} / PF = {res.pf:.3f} / 取引回数 = {res.n}",
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct=(f"四半期末 day別プロファイル（|t|最大は day{best_day:+.0f}）"
                   if best_day is not None else "なし"),
        tables=tables, notes=notes)
    if write:
        rep.write("phase6_quarter_end")
    return rep
