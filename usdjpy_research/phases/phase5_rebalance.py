"""Phase 5: 月末リバランスフローの「量」推定（既存検証の改良版）。

事前登録:
  仮説   : 月末の為替フローは方向だけでなく必要リバランス額を推定でき、
           推定量の大きさがリターンの大きさと対応する。
  UBSの修正（このPhaseの本体）:
           月全体の相対リターンで最後X日のリターンを予測すると説明力を
           過大評価する。そこで「月末X日前までの相対リターンで、
           最後のX日のリターンを予測」する形に改める。X = 3（レポート準拠）。
           既に試した「S&P騰落率条件付き月末Fix」は月全体のリターンを
           使っていた可能性が高く、それがまさに過大評価のパターン。
  推定式 : estimate = 0.5*(米株 − 日株) + 0.8*(米債 − 日債)
           （株ヘッジ比率50%、債券80%。Barclays系モデルの想定）
           米国資産のアウトパフォーム -> ヘッジ必要額増 -> ドル売り円買い
           -> USDJPY は下。よって **売買方向 = −sign(estimate)**。
  観測窓 : 月末3営業日（ME-3 の約定時刻 -> ME の約定時刻）。
           ロンドン16時FIX 版も併記（FIXのサーバー時刻は英米DSTのズレで
           18:00/19:00 に変動するので common.tz が計算する）。
  閾値   : 推定量の絶対値で5分位に分け、**上位1分位のみ**でエントリー。
  判定   : 事前登録された単一仮説のイベントスタディなので
           **パーミュテーション検定**（10,000回・片側2.5%以内）+ PF>=1.3 で判定する
           （v1.1 差分4）。ここでの置換は日付ではなく **推定量そのものの並べ替え**:
           推定量を月末どうしで入れ替えて「上位分位の選択」と「方向の決定」を
           やり直し、実測がその分布のどこに来るかを見る。
           日付を動かすのではなく、月末という枠は固定したまま
           「推定量に情報があるか」だけを検定できるのでこちらが素直。
  検定回数: 回帰1 + 上位分位売買1 + FIX版1 + 介入除外1 + 月全体版(比較用)1
            + レジーム別2 = 7 -> 余裕を見て 8 で補正する。

為替介入への注意（指示書 §7）:
  介入が入るとモデルは壊れる。介入実績が公表されている日は別掲し、
  介入日を除いた数値も必ず出す。

データの置き方（いずれも date,close の2列CSV）:
  usdjpy_research/data/idx_us_equity.csv   S&P500
  usdjpy_research/data/idx_jp_equity.csv   TOPIX
  usdjpy_research/data/idx_us_bond.csv     米国債インデックス
  usdjpy_research/data/idx_jp_bond.csv     日本国債インデックス
  usdjpy_research/config/intervention_days.csv  介入実施日（財務省公表）
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import regime, tz
from ..common.backtest import simulate, series_at_hour
from ..common.cost import CostModel
from ..common.io import load_mt5_bars, resample_bars, load_series_csv, PIP
from ..common.report import PhaseReport
from ..common.stats import (summarize, bonferroni_t, judge, regime_cell,
                            permutation_from_values, PERM_ITERS)

ROOT = Path(__file__).resolve().parent.parent
DATA, CONFIG = ROOT / "data", ROOT / "config"

INDEX_FILES = {
    "us_equity": DATA / "idx_us_equity.csv",
    "jp_equity": DATA / "idx_jp_equity.csv",
    "us_bond":   DATA / "idx_us_bond.csv",
    "jp_bond":   DATA / "idx_jp_bond.csv",
}
HEDGE_EQUITY, HEDGE_BOND = 0.50, 0.80
WINDOW_DAYS = 3          # UBS の修正に合わせた X
N_QUANTILES = 5
N_TESTS = 8
DEFAULT_EXEC_HOUR = 17
DEFAULT_SL_PIPS = 120.0


def _missing_report(missing, write: bool) -> PhaseReport:
    rep = PhaseReport(
        phase="Phase 5", axis="月末リバランスフローの量推定（UBS修正版）",
        hypothesis="月末3営業日前までの相対リターンから推定したリバランス量が、"
                   "最後3営業日の USDJPY リターンを説明する",
        preregistered=f"株50%・債券80%のヘッジ比率 / 窓 = 月末{WINDOW_DAYS}営業日 /"
                      f" 推定量の絶対値 上位{N_QUANTILES}分の1のみエントリー /"
                      f" 方向 = −sign(estimate) / 検定{N_TESTS}回",
        n_tests=N_TESTS, bonferroni_crit_t=bonferroni_t(N_TESTS),
        result_after_cost="未実行（指数データ未取得）",
        regime_breakdown="未実行", neighborhood="未実行",
        verdict="未実行",
        reason=f"指数データが無いため実行できない: {', '.join(missing)}",
        byproduct="なし",
        notes=[
            "次の4本を date,close の2列CSVで置いてください（yfinance 等で取得可）:",
            *[f"  {k}: {v}" for k, v in INDEX_FILES.items()],
            "債券インデックスは総合リターン指数（price only ではなく total return）を"
            "使ってください。クーポンを落とすとヘッジ必要額の推定がずれます。",
            "介入日は config/intervention_days.csv（date列）に入れると自動で別掲されます。",
        ])
    if write:
        rep.write("phase5_rebalance")
    return rep


def _month_ends(tdays: pd.DatetimeIndex) -> pd.DatetimeIndex:
    s = pd.Series(tdays, index=tdays)
    return pd.DatetimeIndex(sorted(s.groupby([tdays.year, tdays.month]).max().values))


def _ret(s: pd.Series, a: pd.Timestamp, b: pd.Timestamp):
    """a -> b の単純リターン。直近値で埋める（休場日の違いを吸収）。"""
    try:
        pa = s.asof(a)
        pb = s.asof(b)
    except Exception:
        return np.nan
    if not (pa and pb) or pa != pa or pb != pb or pa == 0:
        return np.nan
    return pb / pa - 1.0


def _ols_t(x: np.ndarray, y: np.ndarray):
    """単回帰 y = a + b x の (beta, t, R2)。"""
    m = np.isfinite(x) & np.isfinite(y)
    x, y = x[m], y[m]
    n = x.size
    if n < 10 or x.std() == 0:
        return np.nan, np.nan, np.nan, n
    b, a = np.polyfit(x, y, 1)
    yhat = a + b * x
    resid = y - yhat
    s2 = resid @ resid / (n - 2)
    se = np.sqrt(s2 / ((x - x.mean()) @ (x - x.mean())))
    ss_tot = ((y - y.mean()) ** 2).sum()
    r2 = 1 - (resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return float(b), float(b / se), float(r2), int(n)


def run(bars_path: str, cost: CostModel, *, exec_hour: int = DEFAULT_EXEC_HOUR,
        sl_pips: float = DEFAULT_SL_PIPS, n_iter: int = PERM_ITERS,
        write: bool = True) -> PhaseReport:
    missing = [k for k, v in INDEX_FILES.items() if not v.exists()]
    if missing:
        return _missing_report(missing, write)

    # 月末3営業日の保有なので1時間足で足りる
    df = resample_bars(load_mt5_bars(bars_path), "1h")
    px = series_at_hour(df, exec_hour)
    px.index = px.index.normalize()
    px = px[~px.index.duplicated(keep="first")]
    tdays = pd.DatetimeIndex(sorted(px.index))
    idx = {k: load_series_csv(v) for k, v in INDEX_FILES.items()}

    interv = set()
    f = CONFIG / "intervention_days.csv"
    if f.exists():
        iv = pd.read_csv(f, comment="#")
        iv.columns = [c.strip().lower() for c in iv.columns]
        interv = set(pd.to_datetime(iv["date"], errors="coerce").dropna().dt.normalize())

    tables, notes = [], [b for b in [regime.warning_banner().strip()] if b]
    notes.append(f"推定は『前月末 -> 月末{WINDOW_DAYS}営業日前』の相対リターンを使い、"
                 f"予測対象は『月末{WINDOW_DAYS}営業日』のリターン。窓が重ならないので"
                 "UBSの指摘する過大評価を避けられる。")
    notes.append(f"介入日 {len(interv)} 件を config/intervention_days.csv から読み込んだ。"
                 if interv else
                 "config/intervention_days.csv が無いため介入日の別掲をスキップした。")

    me = _month_ends(tdays)
    pos = {d: i for i, d in enumerate(tdays)}
    recs = []
    for k in range(1, len(me)):
        prev_me, cur_me = me[k - 1], me[k]
        i = pos[cur_me]
        j = i - WINDOW_DAYS
        if j <= pos[prev_me]:
            continue
        start = tdays[j]                       # ME-3（この日の約定時刻にエントリー）
        est = (HEDGE_EQUITY * (_ret(idx["us_equity"], prev_me, start)
                               - _ret(idx["jp_equity"], prev_me, start))
               + HEDGE_BOND * (_ret(idx["us_bond"], prev_me, start)
                               - _ret(idx["jp_bond"], prev_me, start)))
        # 比較用: 月全体のリターンを使った旧来版（過大評価するはずの形）
        est_full = (HEDGE_EQUITY * (_ret(idx["us_equity"], prev_me, cur_me)
                                    - _ret(idx["jp_equity"], prev_me, cur_me))
                    + HEDGE_BOND * (_ret(idx["us_bond"], prev_me, cur_me)
                                    - _ret(idx["jp_bond"], prev_me, cur_me)))
        target = (px.get(cur_me, np.nan) - px.get(start, np.nan)) / PIP
        recs.append({"month_end": cur_me, "start": start, "est": est,
                     "est_full": est_full, "target_pips": target,
                     "intervened": any(d in interv for d in tdays[j:i + 1])})
    panel = pd.DataFrame(recs).dropna(subset=["est", "target_pips"])

    # --- (1) 回帰 ---
    b, t, r2, n = _ols_t(panel["est"].to_numpy(), panel["target_pips"].to_numpy())
    bf, tf, r2f, nf = _ols_t(panel["est_full"].to_numpy(), panel["target_pips"].to_numpy())
    sub = panel[~panel["intervened"]]
    bx, tx, r2x, nx = _ols_t(sub["est"].to_numpy(), sub["target_pips"].to_numpy())
    tables.append(("回帰: 月末3営業日リターン(pips) ~ 推定リバランス量", "\n".join([
        f"UBS修正版（ME-3 までの相対リターン）: beta={b:.1f}  t={t:.3f}  R2={r2:.4f}  n={n}",
        f"  介入日を除く                      : beta={bx:.1f}  t={tx:.3f}  R2={r2x:.4f}  n={nx}",
        f"旧来版（月全体の相対リターン・比較用）: beta={bf:.1f}  t={tf:.3f}  R2={r2f:.4f}  n={nf}",
        "",
        "旧来版の R2 が UBS修正版より明確に高いなら、それは説明力ではなく",
        "窓の重なりによる過大評価。既存検証がこの形だったなら結果を破棄すること。",
        "beta が負なら『米国資産アウトパフォーム -> USDJPY 下落』で仮説と整合。",
    ])))

    # --- (2) 上位分位での売買 ---
    panel = panel.copy()
    panel["abs_est"] = panel["est"].abs()
    panel["q"] = pd.qcut(panel["abs_est"], N_QUANTILES, labels=False, duplicates="drop")
    top = panel[panel["q"] == panel["q"].max()]
    qtbl = panel.groupby("q").apply(
        lambda g: pd.Series({
            "n": len(g),
            "平均|推定量|": g["abs_est"].mean(),
            "方向調整後平均pips": (-np.sign(g["est"]) * g["target_pips"]).mean(),
        }), include_groups=False)
    tables.append((f"推定量の絶対値 {N_QUANTILES}分位別（控除前・参考値）",
                   qtbl.round(4).to_string()
                   + "\n※分位が上がるほど方向調整後の平均が伸びるのが仮説どおりの形。"))

    def _sigs(rows):
        for _, r in rows.iterrows():
            d0 = int(-np.sign(r["est"]))
            if d0 == 0:
                continue
            yield (_dt.datetime.combine(r["start"].date(), _dt.time(exec_hour, 0)),
                   _dt.datetime.combine(r["month_end"].date(), _dt.time(exec_hour, 0)),
                   d0, str(r["month_end"].date()))

    trades = simulate(df, _sigs(top), cost, sl_pips=sl_pips)
    res = summarize(trades["net_pips"] if len(trades) else [])
    gross = summarize(trades["gross_pips"] if len(trades) else [])
    tr_ex = simulate(df, _sigs(top[~top["intervened"]]), cost, sl_pips=sl_pips)
    res_ex = summarize(tr_ex["net_pips"] if len(tr_ex) else [])
    tables.append((f"上位{N_QUANTILES}分の1でのエントリー（コスト控除後が判定対象）", "\n".join([
        f"取引回数     : {res.n}" + ("   <- 月次シグナルの上位分位なので構造的に少ない。"
                                     if res.n < 100 else ""),
        f"平均(控除後) : {res.mean_pips:.2f} pips / 控除前 {gross.mean_pips:.2f} pips",
        f"t値 (控除後) : {res.t:.3f}           / 介入日除外 {res_ex.t:.3f} (n={res_ex.n})",
        f"PF  (控除後) : {res.pf:.3f}           / 介入日除外 {res_ex.pf:.3f}",
        f"勝率         : {res.win_rate*100:.1f}%",
    ])))

    # --- (3) ロンドン16時FIX 版 ---
    fix_sigs = []
    for _, r in top.iterrows():
        d0 = int(-np.sign(r["est"]))
        if d0 == 0:
            continue
        i = pos[r["month_end"]]
        for k in range(WINDOW_DAYS):
            day = tdays[i - k]
            h = tz.london_fix_server_hour(day.date())
            fix_sigs.append((_dt.datetime.combine(day.date(), _dt.time(h - 1, 0)),
                             _dt.datetime.combine(day.date(), _dt.time(min(h + 1, 23), 0)),
                             d0, f"{day.date()}|fix{h}"))
    tr_fix = simulate(df, fix_sigs, cost, sl_pips=sl_pips)
    res_fix = summarize(tr_fix["net_pips"] if len(tr_fix) else [])
    tables.append(("ロンドン16時FIX 前後2時間のみ保有（コスト控除後）", "\n".join([
        f"取引回数 : {res_fix.n}",
        f"平均     : {res_fix.mean_pips:.2f} pips   t={res_fix.t:.3f}   PF={res_fix.pf:.3f}",
        "",
        "FIXのサーバー時刻は英米DSTのズレで 18:00 / 19:00 を行き来する"
        "（common.tz.london_fix_server_hour が年ごとに計算している）。",
        "ここを 18:00 固定で回すと、3月下旬と10月下旬の取引が1時間ずれる。",
    ])))

    # --- (4) レジーム別 ---
    regime_str = "取引なし"
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        cells = [f"{name}: {regime_cell(summarize(grp['net_pips']))}"
                 for name, grp in trades.groupby(lab.to_numpy())]
        tables.append(("円高期 / 円安期 別（コスト控除後）", "\n".join(cells)
                       + "\n\nレジーム別は参考値であり、単独では合否判定に使わない"
                         "（v1.1 差分4）。"))
        regime_str = " / ".join(cells)

    # --- (5) 近傍安定性: 窓を ±1日 ---
    stab = []
    for shift in (-1, +1):
        s2 = []
        for _, r in top.iterrows():
            i, d0 = pos[r["month_end"]], int(-np.sign(r["est"]))
            j = i - WINDOW_DAYS + shift
            if d0 == 0 or j < 0 or j >= i:
                continue
            s2.append((_dt.datetime.combine(tdays[j].date(), _dt.time(exec_hour, 0)),
                       _dt.datetime.combine(tdays[i].date(), _dt.time(exec_hour, 0)), d0, ""))
        tr = simulate(df, s2, cost, sl_pips=sl_pips)
        r = summarize(tr["net_pips"] if len(tr) else [])
        stab.append([f"{WINDOW_DAYS + shift}営業日", r.n, round(r.mean_pips, 2), round(r.t, 3)])
    tables.append(("近傍安定性（窓を±1営業日）",
                   pd.DataFrame([[f"{WINDOW_DAYS}営業日", res.n, round(res.mean_pips, 2),
                                  round(res.t, 3)]] + stab,
                                columns=["窓", "n", "平均pips", "t"]).to_string(index=False)))
    signs = {np.sign(v) for v in [res.mean_pips] + [s[2] for s in stab] if v == v}
    stable = len(signs) == 1

    # --- 差分4: 推定量の並べ替えによるパーミュテーション検定 ---
    # 月末という枠は固定したまま、推定量を月末どうしで入れ替える。
    # 「上位分位の選択」と「方向の決定」をやり直して同じ統計量を作り直す。
    long_pnl, short_pnl = {}, {}
    for d0 in (+1, -1):
        sig_all = [( _dt.datetime.combine(r["start"].date(), _dt.time(exec_hour, 0)),
                     _dt.datetime.combine(r["month_end"].date(), _dt.time(exec_hour, 0)),
                     d0, str(r["month_end"].date()))
                   for _, r in panel.iterrows()]
        tr_all = simulate(df, sig_all, cost, sl_pips=sl_pips)
        book = dict(zip(tr_all["tag"], tr_all["net_pips"])) if len(tr_all) else {}
        (long_pnl if d0 > 0 else short_pnl).update(book)

    keys = [str(x.date()) for x in panel["month_end"]]
    have = np.array([k in long_pnl and k in short_pnl for k in keys])
    pl = np.array([long_pnl.get(k, np.nan) for k in keys])
    ps = np.array([short_pnl.get(k, np.nan) for k in keys])
    est_arr = panel["est"].to_numpy()
    n_top = max(1, int(round(len(est_arr) / N_QUANTILES)))

    def _stat(est_v: np.ndarray) -> float:
        order = np.argsort(-np.abs(est_v))
        sel = [i for i in order if have[i]][:n_top]
        if not sel:
            return np.nan
        return float(np.mean([(pl[i] if est_v[i] < 0 else ps[i]) for i in sel]))

    rng_p = np.random.default_rng(20260915)
    observed_stat = _stat(est_arr)
    null_vals = [_stat(rng_p.permutation(est_arr)) for _ in range(n_iter)]
    perm = permutation_from_values(observed_stat, [v for v in null_vals if v == v])
    tables.append((f"パーミュテーション検定（v1.1 差分4・{n_iter:,}回）",
                   perm.summary() + "\n\n"
                   "推定量を月末どうしで並べ替え、『上位分位の選択』と『方向の決定』を"
                   "やり直したときの分布。\n"
                   "月末という枠は固定したままなので、『月末に何かある』ことではなく"
                   "『推定量に情報があるか』だけを検定できる。"))

    crit = bonferroni_t(N_TESTS)
    ok, reason = judge(res, kind="event", perm=perm)
    if ok and abs(res_ex.t) < 2 and res_ex.n >= 10:
        ok, reason = False, (f"介入日を除くと t={res_ex.t:.2f} に落ちる。介入に依存した結果。")
    if ok and not stable:
        ok, reason = False, "窓を±1営業日ずらすと符号が反転する。"
    verdict = "合格" if ok else "不合格"
    reason += (f"（UBS修正版の回帰 t={t:.2f}/R2={r2:.4f}、"
               f"旧来版 R2={r2f:.4f}）")

    rep = PhaseReport(
        phase="Phase 5", axis="月末リバランスフローの量推定（UBS修正版）",
        hypothesis="月末3営業日前までの相対リターンから推定したリバランス量が、"
                   "最後3営業日の USDJPY リターンを説明する",
        preregistered=f"ヘッジ比率 株{int(HEDGE_EQUITY*100)}%・債{int(HEDGE_BOND*100)}% /"
                      f" 窓 {WINDOW_DAYS}営業日 / 上位{N_QUANTILES}分の1のみ /"
                      f" 方向 −sign(estimate) / 検定{N_TESTS}回",
        n_tests=N_TESTS, bonferroni_crit_t=crit,
        judgment_method=f"パーミュテーション検定 {n_iter:,}回"
                        "（推定量の並べ替え）+ PF>=1.3",
        permutation=perm.summary().replace("\n", " / "),
        result_after_cost=f"片側p = {perm.p_one_sided:.4f}"
                          f"（{perm.percentile:.1f}パーセンタイル）/ "
                          f"PF = {res.pf:.3f} / 取引回数 = {res.n} / t = {res.t:.3f}"
                          f"（介入日除外 t = {res_ex.t:.3f}）",
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct=f"UBS修正版 R2={r2:.4f} vs 旧来版 R2={r2f:.4f}（過大評価の大きさが分かる）",
        tables=tables, notes=notes)
    if write:
        rep.write("phase5_rebalance")
    return rep
