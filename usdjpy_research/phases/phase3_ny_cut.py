"""Phase 3: NYオプションカット後のレジーム変化。

事前登録:
  仮説   : サーバー17:00（=NY10:00、Phase 0-2 で年間固定と確認済み）を境に、
           USDJPY のボラティリティと方向性の性質が変わる。カット前は
           大口ストライクに引き寄せられて値動きが抑制され、カット後に
           その抑制が外れてブレイクアウトが出やすい。
  観測窓 : カット前1時間 = 16:00〜16:59 / カット後1時間 = 17:00〜17:59（サーバー時間）
  閾値（走らせる前に固定）:
    - 「レンジが狭い日」= カット前1時間のレンジが、**直前20営業日**の
      カット前1時間レンジの **下位25パーセンタイル以下**（当日を含めない）
    - ブレイクアウト判定幅 = カット前1時間の高値+3pips / 安値-3pips
    - 決済 = カット後1時間の終わり（17:59）
    - SL = カット前1時間レンジの反対側（レンジ幅 + 3pips）、最低20pips
  検定回数: ボラ比較1 + 狭レンジ日ブレイク1 + 方向一致/逆行2 + レジーム別2 = 6

オプションの建玉データは使わない（指示書の指定どおり、price action のみ）。
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from ..common import regime
from ..common.backtest import simulate_stop_entry
from ..common.cost import CostModel
from ..common.io import load_mt5_bars, PIP
from ..common.report import PhaseReport
from ..common.stats import summarize, bonferroni_t, TestCounter

# --- 事前登録した定数（実行後に変更禁止） ---
CUT_HOUR = 17
PRE_HOUR = 16
LOOKBACK_DAYS = 20
NARROW_PCTL = 0.25
BREAK_BUFFER_PIPS = 3.0
MIN_SL_PIPS = 20.0
N_TESTS = 6


def _hour_stats(df: pd.DataFrame, hour: int) -> pd.DataFrame:
    """指定した「時」の 1 時間分を日別に集計する。"""
    sel = df[df.index.hour == hour]
    if sel.empty:
        return pd.DataFrame()
    g = sel.groupby(pd.DatetimeIndex(sel.index.date))
    out = pd.DataFrame({
        "high": g["high"].max(), "low": g["low"].min(),
        "open": g["open"].first(), "close": g["close"].last(),
        "bars": g.size(),
    })
    out["range_pips"] = (out["high"] - out["low"]) / PIP
    out["move_pips"] = (out["close"] - out["open"]) / PIP
    # 実現ボラ（バー間リターンの標準偏差, pips）
    out["rv_pips"] = g["close"].apply(
        lambda s: (s.diff().dropna() / PIP).std(ddof=1) if len(s) > 2 else np.nan)
    out.index.name = "date"
    return out


def run(bars_path: str, cost: CostModel, *, write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
    pre = _hour_stats(df, PRE_HOUR)
    post = _hour_stats(df, CUT_HOUR)
    days = pre.index.intersection(post.index)
    pre, post = pre.loc[days], post.loc[days]

    counter = TestCounter()
    tables, notes = [], [b for b in [regime.warning_banner().strip()] if b]
    notes.append(f"カット時刻はサーバー{CUT_HOUR}:00 固定。Phase 0-2 で "
                 "米国イベントが年間固定であることを確認してから使うこと。")

    # --- (1) カット前後のボラ比較 ---
    counter.count("カット前後のボラ比較")
    cmp_tbl = pd.DataFrame({
        f"カット前 {PRE_HOUR}:00-{PRE_HOUR}:59": [pre["range_pips"].mean(),
                                                 pre["range_pips"].median(),
                                                 pre["rv_pips"].mean()],
        f"カット後 {CUT_HOUR}:00-{CUT_HOUR}:59": [post["range_pips"].mean(),
                                                 post["range_pips"].median(),
                                                 post["rv_pips"].mean()],
    }, index=["平均レンジ(pips)", "中央レンジ(pips)", "平均実現ボラ(pips)"]).round(3)
    cmp_tbl["後/前"] = (cmp_tbl.iloc[:, 1] / cmp_tbl.iloc[:, 0]).round(3)
    tables.append(("カット前後1時間のボラティリティ比較", cmp_tbl.to_string()))

    d = (post["range_pips"] - pre["range_pips"]).dropna()
    r_vol = summarize(d)
    tables.append(("レンジ差（カット後 − カット前, pips）の検定",
                   f"n={r_vol.n}  平均={r_vol.mean_pips:.3f}pips  t={r_vol.t:.3f}\n"
                   "※これはコストのかからない記述統計。売買の合否判定には使わない。"))

    yearly = pd.DataFrame({
        "前レンジ": pre.groupby(pre.index.year)["range_pips"].mean(),
        "後レンジ": post.groupby(post.index.year)["range_pips"].mean()})
    yearly["後/前"] = (yearly["後レンジ"] / yearly["前レンジ"])
    tables.append(("年別 レンジ比較（古い期間は参考値: 指示書 §0-4）",
                   yearly.round(3).to_string()))

    # --- (2) 狭レンジ日のブレイクアウト ---
    thr = (pre["range_pips"].shift(1)
           .rolling(LOOKBACK_DAYS, min_periods=LOOKBACK_DAYS)
           .quantile(NARROW_PCTL))
    narrow = (pre["range_pips"] <= thr) & thr.notna()
    n_setup = int(narrow.sum())
    notes.append(f"狭レンジ日の判定に使った直前{LOOKBACK_DAYS}営業日の分位は当日を含まない"
                 "（先読み防止）。")

    setups = []
    for day in pre.index[narrow]:
        hi, lo = pre.loc[day, "high"], pre.loc[day, "low"]
        rng = (hi - lo) / PIP
        sl = max(MIN_SL_PIPS, rng + BREAK_BUFFER_PIPS)
        w0 = _dt.datetime.combine(day.date(), _dt.time(CUT_HOUR, 0))
        w1 = _dt.datetime.combine(day.date(), _dt.time(CUT_HOUR, 59))
        xt = _dt.datetime.combine(day.date(), _dt.time(CUT_HOUR, 59))
        pre_dir = np.sign(pre.loc[day, "move_pips"])
        for direction, trig in ((+1, hi + BREAK_BUFFER_PIPS * PIP),
                                (-1, lo - BREAK_BUFFER_PIPS * PIP)):
            tag = ("一致" if direction == pre_dir else
                   "逆行" if pre_dir != 0 else "前方向なし")
            setups.append((w0, w1, direction, trig, xt, f"{day.date()}|{tag}|{sl:.1f}"))

    # SL はセットアップごとに違うので、SL 幅でグループ分けして回す
    frames = []
    for sl_val in sorted({float(s[5].split("|")[2]) for s in setups}):
        sub = [s for s in setups if float(s[5].split("|")[2]) == sl_val]
        frames.append(simulate_stop_entry(df, sub, cost, sl_pips=sl_val,
                                          entry_slippage_pips=1.0,
                                          sl_slippage_pips=1.0))
    trades = (pd.concat(frames, ignore_index=True).sort_values("entry_time")
              .reset_index(drop=True) if frames else pd.DataFrame())

    counter.count("狭レンジ日ブレイクアウト（全体）")
    res = summarize(trades["net_pips"] if len(trades) else [])
    gross = summarize(trades["gross_pips"] if len(trades) else [])
    fill_rate = (len(trades) / (2 * n_setup) * 100) if n_setup else float("nan")
    tables.append(("狭レンジ日ブレイクアウト（コスト控除後が判定対象）", "\n".join([
        f"セットアップ日数 : {n_setup}（1日につき上下2方向 = {2*n_setup} 本の逆指値）",
        f"成立本数         : {len(trades)}（成立率 {fill_rate:.1f}%）",
        f"平均(控除後)     : {res.mean_pips:.2f} pips  / 控除前 {gross.mean_pips:.2f} pips",
        f"t値 (控除後)     : {res.t:.3f}            / 控除前 {gross.t:.3f}",
        f"PF  (控除後)     : {res.pf:.3f}            / 控除前 {gross.pf:.3f}",
        f"勝率             : {res.win_rate*100:.1f}%",
        f"SL到達率         : {trades['hit_sl'].mean()*100:.1f}%" if len(trades) else "",
        "",
        "上下両方に逆指値を置く設計なので、同日に両方成立する『往復ビンタ』も"
        "そのまま損益に入っている（都合の良い片方だけ数えていない）。",
    ])))

    # --- (3) 方向一致 / 逆行 ---
    dir_str = "取引なし"
    if len(trades):
        kind = trades["tag"].str.split("|").str[1]
        rows = []
        for name in ("一致", "逆行"):
            counter.count(f"方向{name}")
            r = summarize(trades.loc[kind == name, "net_pips"])
            rows.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
        tables.append(("ブレイク方向 × カット前の値動き方向（コスト控除後）",
                       pd.DataFrame(rows, columns=["区分", "n", "平均pips", "t", "PF"])
                       .to_string(index=False)))
        dir_str = " / ".join(f"{r[0]}: t={r[3]} n={r[1]}" for r in rows)

    # --- (4) レジーム別 ---
    regime_str = "取引なし"
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        rows = []
        for name, grp in trades.groupby(lab.to_numpy()):
            counter.count(f"レジーム {name}")
            r = summarize(grp["net_pips"])
            rows.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
        tables.append(("円高期 / 円安期 別（コスト控除後）",
                       pd.DataFrame(rows, columns=["期", "n", "平均pips", "t", "PF"])
                       .to_string(index=False)))
        regime_str = " / ".join(f"{r[0]}: t={r[3]} PF={r[4]} n={r[1]}" for r in rows)

    # --- (5) 近傍安定性: カット時刻を ±1時間ずらす ---
    stab = []
    for shift in (-1, +1):
        h = CUT_HOUR + shift
        p2 = _hour_stats(df, h - 1)
        if p2.empty:
            continue
        thr2 = (p2["range_pips"].shift(1).rolling(LOOKBACK_DAYS, min_periods=LOOKBACK_DAYS)
                .quantile(NARROW_PCTL))
        nr2 = (p2["range_pips"] <= thr2) & thr2.notna()
        st2 = []
        for day in p2.index[nr2]:
            hi, lo = p2.loc[day, "high"], p2.loc[day, "low"]
            sl = max(MIN_SL_PIPS, (hi - lo) / PIP + BREAK_BUFFER_PIPS)
            w0 = _dt.datetime.combine(day.date(), _dt.time(h, 0))
            w1 = xt = _dt.datetime.combine(day.date(), _dt.time(h, 59))
            for dr, tg in ((+1, hi + BREAK_BUFFER_PIPS * PIP), (-1, lo - BREAK_BUFFER_PIPS * PIP)):
                st2.append((w0, w1, dr, tg, xt, f"{day.date()}|x|{sl:.1f}"))
        fr = [simulate_stop_entry(df, [s for s in st2 if float(s[5].split("|")[2]) == v],
                                  cost, sl_pips=v, entry_slippage_pips=1.0, sl_slippage_pips=1.0)
              for v in sorted({float(s[5].split("|")[2]) for s in st2})]
        t2 = pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()
        r = summarize(t2["net_pips"] if len(t2) else [])
        stab.append([f"{shift:+d}時間", r.n, round(r.mean_pips, 2), round(r.t, 3)])
    tables.append(("近傍安定性（カット時刻を±1時間ずらす）",
                   pd.DataFrame([["基準(17:00)", res.n, round(res.mean_pips, 2), round(res.t, 3)]]
                                + stab, columns=["カット時刻", "n", "平均pips", "t"])
                   .to_string(index=False)))
    signs = {np.sign(v) for v in [res.mean_pips] + [s[2] for s in stab] if v == v}
    stable = len(signs) == 1

    n_tests = max(N_TESTS, len(counter))
    crit = bonferroni_t(n_tests)
    chk = res.passes(n_tests)
    verdict = "合格" if (chk["overall"] and stable) else "不合格"
    if verdict == "不合格":
        if res.n < 100:
            reason = f"成立本数 {res.n} で基準の100回未満。"
        elif abs(res.t) < 2:
            reason = (f"コスト控除後 t={res.t:.2f}（控除前 {gross.t:.2f}）で |t|>=2 未満。"
                      "ブレイクアウトは往復ビンタのコスト負担が重い。")
        elif res.pf < 1.3:
            reason = f"コスト控除後 PF={res.pf:.2f} で基準の1.3未満。"
        else:
            reason = "カット時刻を±1時間ずらすと符号が反転する（17:00 特有とは言えない）。"
    else:
        reason = (f"控除後 t={res.t:.2f}/PF={res.pf:.2f}/n={res.n}、"
                  f"カット時刻±1時間でも符号が安定。")

    rep = PhaseReport(
        phase="Phase 3", axis="NYオプションカット後のレジーム変化",
        hypothesis="サーバー17:00 のカットを境にボラと方向性の性質が変わり、"
                   "カット前が狭レンジの日ほどカット後にブレイクアウトが出やすい",
        preregistered=f"カット前1h={PRE_HOUR}:00台 / カット後1h={CUT_HOUR}:00台 /"
                      f" 狭レンジ=直前{LOOKBACK_DAYS}営業日の下位{int(NARROW_PCTL*100)}% /"
                      f" ブレイク幅=高安±{BREAK_BUFFER_PIPS:.0f}pips /"
                      f" SL=レンジ幅+{BREAK_BUFFER_PIPS:.0f}pips（最低{MIN_SL_PIPS:.0f}pips）",
        n_tests=n_tests, bonferroni_crit_t=crit,
        result_after_cost=f"t = {res.t:.3f} / PF = {res.pf:.3f} / 成立本数 = {res.n}",
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct=f"カット前後のボラ比 {cmp_tbl.loc['平均レンジ(pips)','後/前']:.2f}倍 "
                  f"と年別推移（方向不問のボラ戦略の材料にはなる）/ 方向別内訳: {dir_str}",
        tables=tables, notes=notes)
    if write:
        rep.write("phase3_ny_cut")
    return rep
