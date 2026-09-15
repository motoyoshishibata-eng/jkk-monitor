"""Phase 6: 四半期末のドル調達需給。

事前登録:
  仮説   : 四半期末・年末は規制上のバランスシート制約からドル調達コストが
           跳ね上がり、その需給がスポットに波及する。
  観測窓 : 四半期末（3・6・9・12月の最終営業日）を day 0 とし、営業日で ±5 日。
  方向   : 決め打ちしない（両側t検定）。
  判定   : 事前登録された単一仮説のイベントスタディなので
           **パーミュテーション検定**（10,000回・曜日月を層化・片側2.5%以内）
           + PF>=1.3（コスト控除後）で判定する（v1.1 差分4）。
  プール検定（v1.1 差分4）:
           四半期末のドル調達は USD 側の現象なので EURUSD / GBPUSD / AUDUSD を
           プールして検定してよい。円クロスは円側要因が混入するので入れない。
             プールで有意 -> USDJPY 単体で再確認 -> 単体でも同符号なら合格
             プールで無意 -> Phase 6 打ち切り（USDJPY単体を深追いしない）
  検定回数: day別 11 + 集約1 + 年末別掲2 + 2015年前後2 = 16。

打ち切り判断（指示書 §8）:
  日付ダミーで何も出なければ、ベーシスの有料データを買ってまで深追いしない。
  このPhaseは早めに打ち切ること。

構造的な制約:
  四半期末は年4回しかないので、2007年起点でも取引回数は最大80回程度。
  v1 の「100回以上」基準では原理的に判定不能だった。v1.1 差分4 で
  パーミュテーション検定に切り替えたことでこの制約は解消している
  （検定方法を仮説の形に合わせただけで、基準を緩めたわけではない）。
  ただし PF>=1.3・コスト控除後・レジーム別掲の要件はそのまま維持する。

プール検定用のデータ:
  usdjpy_research/data/EURUSD_M1.csv / GBPUSD_M1.csv / AUDUSD_M1.csv
  （USDJPY と同じ MT5 バーCSV 形式。無ければプール検定はスキップされる）
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pandas as pd

from pathlib import Path

from ..common import regime
from ..common.backtest import simulate, series_at_hour
from ..common.cost import CostModel
from ..common.io import load_mt5_bars, resample_bars, PIP
from ..common.report import PhaseReport
from ..common.stats import (summarize, bonferroni_t, TestCounter, judge,
                            regime_cell, permutation_test,
                            permutation_test_pooled, PERM_ITERS)

OFFSETS = list(range(-5, 6))
AGG_WINDOW = (-3, 0)      # 需給圧力が積み上がる区間として事前登録
N_TESTS = 16
DEFAULT_EXEC_HOUR = 17
DEFAULT_SL_PIPS = 100.0
STRUCTURE_BREAK_YEAR = 2015   # 日銀レビューが指摘する構造変化
POOL_SYMBOLS = ("EURUSD", "GBPUSD", "AUDUSD")   # 円クロスは入れない
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


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


def _window_contrib(df: pd.DataFrame, tdays: pd.DatetimeIndex, cost: CostModel,
                    exec_hour: int, sl_pips: float, span: int) -> pd.Series:
    """全営業日について「span営業日前から当日までロング」の損益を作る。

    パーミュテーション検定の候補（＝四半期末がその日だったら、の損益）。
    """
    sigs = []
    for i in range(span, len(tdays)):
        sigs.append((_dt.datetime.combine(tdays[i - span].date(), _dt.time(exec_hour, 0)),
                     _dt.datetime.combine(tdays[i].date(), _dt.time(exec_hour, 0)),
                     +1, str(tdays[i].date())))
    tr = simulate(df, sigs, cost, sl_pips=sl_pips)
    if not len(tr):
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([]))
    c = tr.groupby("tag")["net_pips"].sum()
    c.index = pd.DatetimeIndex(c.index)
    return c.sort_index()


def run(bars_path: str, cost: CostModel, *, exec_hour: int = DEFAULT_EXEC_HOUR,
        sl_pips: float = DEFAULT_SL_PIPS, n_iter: int = PERM_ITERS,
        write: bool = True) -> PhaseReport:
    # 日をまたぐ保有なので1時間足で足りる（SL到達判定は M1 と同じ答えになる）
    df = resample_bars(load_mt5_bars(bars_path), "1h")
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
        cells = [f"{name}: {regime_cell(summarize(grp['net_pips']))}"
                 for name, grp in trades.groupby(lab.to_numpy())]
        tables.append(("円高期 / 円安期 別（コスト控除後）", "\n".join(cells)
                       + "\n\nレジーム別は参考値であり、単独では合否判定に使わない"
                         "（v1.1 差分4）。"))
        regime_str = " / ".join(cells)

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

    # --- 差分4: USDJPY 単体のパーミュテーション検定 ---
    span = b - a + 1      # day(a-1) にエントリーし day b で決済する保有営業日数
    contrib = _window_contrib(df, tdays, cost, exec_hour, sl_pips, span)
    perm = permutation_test(contrib, anchors, n_iter=n_iter)
    tables.append((f"パーミュテーション検定 / USDJPY単体（v1.1 差分4・{n_iter:,}回）",
                   perm.summary() + "\n\n"
                   "四半期末をランダムな営業日に置き換え、曜日・月を実測に合わせて"
                   "層化抽出している。\n"
                   "※四半期末は3・6・9・12月の月末に固まるので、月を層化すると"
                   "候補が『同じ月の営業日』に限られる。層内の候補数が少ない場合は"
                   "その旨が下に出る。"
                   + (f"\n注意: {perm.strata_note}" if perm.strata_note else "")))

    # --- 差分4: EURUSD / GBPUSD / AUDUSD をプールした検定 ---
    pooled, missing = {}, []
    for sym in POOL_SYMBOLS:
        f = DATA_DIR / f"{sym}_M1.csv"
        if not f.exists():
            missing.append(sym)
            continue
        d2 = resample_bars(load_mt5_bars(f), "1h")
        td2 = pd.DatetimeIndex(sorted(set(d2.index.normalize())))
        pooled[sym] = _window_contrib(d2, td2, cost, exec_hour, sl_pips, span)
    perm_pool = None
    if pooled:
        perm_pool = permutation_test_pooled(pooled, anchors, n_iter=n_iter)
        tables.append((f"プール検定 {'/'.join(pooled)}（v1.1 差分4・{n_iter:,}回）",
                       perm_pool.summary() + "\n\n"
                       "四半期末のドル調達は USD 側の現象なので、ドルストレートを"
                       "プールして検定している。円クロスは円側要因が混入するため入れない。\n"
                       "1回の置換では全ペアに同じランダム日付を使う"
                       "（ペア間の相関を保たないと帰無分布の分散が過小になるため）。\n"
                       "寄与は候補日全体で標準化してから足している"
                       "（pips のスケールがペアで違うため）。"
                       + (f"\n注意: {perm_pool.strata_note}" if perm_pool.strata_note else "")))
    else:
        notes.append(f"プール検定をスキップした（{', '.join(missing)} のバーCSVが "
                     f"data/ に無い）。差分4 の手順では、まずプールで有意かを見て、"
                     f"無意なら Phase 6 は打ち切りと判断する。"
                     f"USDJPY 単体の結果だけで深追いしないこと。")

    n_tests = max(N_TESTS, len(counter))
    crit = bonferroni_t(n_tests)
    best_day = prof["t"].abs().idxmax() if len(prof) else None
    ok, reason = judge(res, kind="event", perm=perm)

    if perm_pool is not None:
        same_sign = (np.sign(perm_pool.observed) == np.sign(res.mean_pips))
        if not perm_pool.passed:
            ok = False
            reason = (f"プール検定（{'/'.join(pooled)}）が片側p={perm_pool.p_one_sided:.4f} "
                      f"で無意。差分4 の手順どおり Phase 6 は打ち切り、"
                      f"USDJPY 単体は深追いしない。")
        elif not same_sign:
            ok = False
            reason = (f"プール検定は有意（片側p={perm_pool.p_one_sided:.4f}）だが、"
                      f"USDJPY 単体の符号が逆。ドル側の現象が USDJPY に"
                      f"同じ向きで乗っていない。")
        elif ok:
            reason = (f"プール検定 片側p={perm_pool.p_one_sided:.4f} で有意、"
                      f"USDJPY 単体も同符号。" + reason)
    if ok and not stable:
        ok, reason = False, "集約窓を±1営業日ずらすと符号が反転する。"
    verdict = "合格" if ok else "不合格"

    rep = PhaseReport(
        phase="Phase 6", axis="四半期末のドル調達需給",
        hypothesis="四半期末・年末のドル調達需給がスポットに波及する",
        preregistered=f"四半期最終営業日を day0 とし営業日 ±5 / 集約窓 day{a:+d}〜{b:+d} /"
                      f" 両側検定 / 約定サーバー{exec_hour:02d}:00 / SL {sl_pips:.0f}pips",
        n_tests=n_tests, bonferroni_crit_t=crit,
        judgment_method=f"パーミュテーション検定 {n_iter:,}回（曜日・月を層化）+ PF>=1.3"
                        + ("　/　ドルストレート3通貨のプール検定"
                           if perm_pool is not None else "　/　プール検定は未実施"),
        permutation=(perm.summary().replace("\n", " / ")
                     + (f"　||　プール: {perm_pool.summary()}".replace("\n", " / ")
                        if perm_pool is not None else "")),
        result_after_cost=f"片側p = {perm.p_one_sided:.4f}"
                          f"（{perm.percentile:.1f}パーセンタイル）/ "
                          f"PF = {res.pf:.3f} / 取引回数 = {res.n} / t = {res.t:.3f}"
                          + (f" / プール片側p = {perm_pool.p_one_sided:.4f}"
                             if perm_pool is not None else ""),
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict=verdict, reason=reason,
        byproduct=(f"四半期末 day別プロファイル（|t|最大は day{best_day:+.0f}）"
                   if best_day is not None else "なし"),
        tables=tables, notes=notes)
    if write:
        rep.write("phase6_quarter_end")
    return rep
