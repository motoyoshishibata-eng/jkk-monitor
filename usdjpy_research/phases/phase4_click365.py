"""Phase 4: くりっく365建玉（新規性が最も高い軸）。

事前登録:
  仮説   : 日本の個人投資家（逆張り寄り）の建玉偏りが極端になると、
           その方向に踏み上げ／投げが発生する。
           CFTC建玉（海外投機筋・順張り寄り）とは性質が逆なので、
           既に検証済みのCFTCフィルターとは別の情報を持つはず。
  指標   : 買建玉比率 ratio = long / (long + short) の Zスコア（過去250営業日基準、当日含まず）
  閾値（走らせる前に固定、実行後の変更禁止）:
    - Zスコア閾値は **±1.5 と ±2.0 の2通りのみ**
    - 保有期間は **1日・5日・20日の3通りのみ**
    - 方向は2通り（踏み上げ方向 / その逆）
    -> 検定回数 = 2 × 3 × 2 = 12。Bonferroni臨界 |t| ≈ 2.87 を必ず併記する。
  主仮説の方向 : Z <= -閾値（個人が売り越し極端）-> ロング（踏み上げ）
                 Z >= +閾値（個人が買い越し極端）-> ショート（投げ）

先読み防止:
  くりっく365の日報は「前営業日分が午前中に更新」される。したがって
  d 日の建玉で d 日中に売買することはできない。必ず **d+1 営業日**の
  約定時刻でエントリーする。

合格した場合の実装方針:
  CFTCフィルターと同じく **ロット調整型**（取引回数を減らさずロット量を変える）。
  ON/OFF型は前例で口座損益が悪化しているため採用しない。

データの置き方:
  usdjpy_research/data/click365_usdjpy.csv
      date,long_oi,short_oi
      2005-07-01,12345,23456
  くりっく365（東京金融取引所）のヒストリカルデータ / 取引所日報から作る。
  2005年7月1日の上場以降が無料で取れる。
  CFTC との独立性チェック用（任意）:
  usdjpy_research/data/cftc_jpy.csv
      date,net_noncommercial
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import numpy as np
import pandas as pd

from ..common import regime
from ..common.backtest import simulate, series_at_hour
from ..common.cost import CostModel
from ..common.io import load_mt5_bars, load_series_csv
from ..common.report import PhaseReport
from ..common.stats import summarize, zscore, bonferroni_t

DATA = Path(__file__).resolve().parent.parent / "data"
OI_FILE = DATA / "click365_usdjpy.csv"
CFTC_FILE = DATA / "cftc_jpy.csv"

# --- 事前登録した格子（拡張禁止） ---
Z_THRESHOLDS = (1.5, 2.0)
HOLD_DAYS = (1, 5, 20)
DIRECTIONS = ("踏み上げ方向", "逆方向")
N_TESTS = len(Z_THRESHOLDS) * len(HOLD_DAYS) * len(DIRECTIONS)   # = 12
ZSCORE_WINDOW = 250
DEFAULT_EXEC_HOUR = 17
SL_BY_HOLD = {1: 60.0, 5: 120.0, 20: 250.0}


def _missing_report(write: bool) -> PhaseReport:
    rep = PhaseReport(
        phase="Phase 4", axis="くりっく365建玉",
        hypothesis="日本の個人投資家の建玉偏りが極端になると踏み上げ／投げが発生する",
        preregistered=f"Zスコア(250日) 閾値 ±1.5/±2.0 / 保有 1・5・20日 / 方向2通り"
                      f" = 検定{N_TESTS}回",
        n_tests=N_TESTS, bonferroni_crit_t=bonferroni_t(N_TESTS),
        result_after_cost="未実行（建玉データ未取得）",
        regime_breakdown="未実行", neighborhood="未実行",
        verdict="未実行", reason=f"{OI_FILE} が無いため実行できない。",
        byproduct="なし",
        notes=[
            "くりっく365（東京金融取引所）の公式サイトからヒストリカルデータを"
            "一括ダウンロードし、date,long_oi,short_oi の3列CSVにして "
            f"{OI_FILE} に置いてください。2005年7月1日の上場以降が無料で取れます。",
            "取得できたら同じコマンドを再実行するだけで、この Phase は最後まで走ります。",
            "CFTC との独立性チェックを行う場合は data/cftc_jpy.csv "
            "（date,net_noncommercial）も置いてください。相関が高ければ"
            "『新規性なし』としてこのPhaseは打ち切りです。",
        ])
    if write:
        rep.write("phase4_click365")
    return rep


def run(bars_path: str, cost: CostModel, *, exec_hour: int = DEFAULT_EXEC_HOUR,
        write: bool = True) -> PhaseReport:
    if not OI_FILE.exists():
        return _missing_report(write)

    df = load_mt5_bars(bars_path)
    px = series_at_hour(df, exec_hour)
    tdays = pd.DatetimeIndex(sorted(set(px.index.normalize())))

    oi = pd.read_csv(OI_FILE)
    oi.columns = [c.strip().lower() for c in oi.columns]
    oi["date"] = pd.to_datetime(oi["date"])
    oi = oi.dropna(subset=["date"]).sort_values("date").set_index("date")
    ratio = (oi["long_oi"] / (oi["long_oi"] + oi["short_oi"])).dropna()
    z = zscore(ratio, ZSCORE_WINDOW).dropna()

    tables, notes = [], [b for b in [regime.warning_banner().strip()] if b]
    notes.append(f"Zスコアは過去{ZSCORE_WINDOW}営業日基準、当日を含まない（先読み防止）。")
    notes.append("建玉は日報が翌午前更新のため、シグナル日の **翌営業日** の"
                 f"サーバー{exec_hour:02d}:00 でエントリーしている。")
    tables.append(("建玉比率 Zスコアの分布", "\n".join([
        f"期間      : {z.index.min().date()} 〜 {z.index.max().date()}  (n={len(z)})",
        f"平均/標準偏差: {z.mean():.3f} / {z.std():.3f}",
        *[f"|Z| >= {t}: {int((z.abs() >= t).sum())} 日 "
          f"（売り越し極端 {int((z <= -t).sum())} / 買い越し極端 {int((z >= t).sum())}）"
          for t in Z_THRESHOLDS],
    ])))

    # --- CFTC との独立性（新規性の確認） ---
    if CFTC_FILE.exists():
        cftc = load_series_csv(CFTC_FILE)
        j = pd.concat([ratio.rename("click365"), cftc.rename("cftc")], axis=1).dropna()
        if len(j) > 30:
            c_lv = j["click365"].corr(j["cftc"])
            c_df = j.diff().dropna().pipe(lambda x: x["click365"].corr(x["cftc"]))
            tables.append(("CFTC建玉との独立性", "\n".join([
                f"水準の相関  : {c_lv:+.3f}  (n={len(j)})",
                f"変化幅の相関: {c_df:+.3f}",
                "",
                "|相関| が 0.7 を超えるなら CFTC と同じ情報しか持っておらず、"
                "新規性なしとしてこのPhaseは打ち切り。",
                "個人が逆張り・海外投機筋が順張りなら **負の相関** が出るのが自然。",
            ])))
            notes.append(f"CFTCとの水準相関 {c_lv:+.3f} / 変化幅相関 {c_df:+.3f}。")
    else:
        notes.append("data/cftc_jpy.csv が無いため CFTC との独立性チェックをスキップした。"
                     " 新規性の確認は合格判定の前提なので、必ず後で実施すること。")

    # --- 事前登録した 12 通りの格子を全部回す ---
    pos = {d: i for i, d in enumerate(tdays)}
    rows, detail = [], {}
    for thr in Z_THRESHOLDS:
        for hold in HOLD_DAYS:
            for dname in DIRECTIONS:
                sign = +1 if dname == "踏み上げ方向" else -1
                sigs = []
                for sig_date, zv in z.items():
                    if abs(zv) < thr:
                        continue
                    # 売り越し極端(z<=-thr) -> 踏み上げはロング
                    d0 = -np.sign(zv) * sign
                    nxt = tdays.searchsorted(pd.Timestamp(sig_date).normalize(), side="right")
                    if nxt >= len(tdays) or nxt + hold >= len(tdays):
                        continue
                    t0 = _dt.datetime.combine(tdays[nxt].date(), _dt.time(exec_hour, 0))
                    t1 = _dt.datetime.combine(tdays[nxt + hold].date(), _dt.time(exec_hour, 0))
                    sigs.append((t0, t1, int(d0), f"Z={zv:.2f}"))
                tr = simulate(df, sigs, cost, sl_pips=SL_BY_HOLD[hold])
                r = summarize(tr["net_pips"] if len(tr) else [])
                detail[(thr, hold, dname)] = (tr, r)
                rows.append([thr, hold, dname, r.n, round(r.mean_pips, 2),
                             round(r.t, 3), round(r.pf, 3),
                             round(r.win_rate * 100, 1)])
    grid = pd.DataFrame(rows, columns=["Z閾値", "保有日", "方向", "n", "平均pips",
                                       "t", "PF", "勝率%"])
    crit = bonferroni_t(N_TESTS)
    tables.append((f"事前登録した{N_TESTS}通りの結果（コスト控除後）",
                   grid.to_string(index=False)
                   + f"\n\n名目臨界 |t|=2.00 / Bonferroni臨界 |t|={crit:.3f}（検定{N_TESTS}回）"
                     "\n※この表から一番良いものを選んで採用するのは禁止。"
                     "\n　主仮説は『踏み上げ方向』であり、そこで基準を満たすかだけを見る。"))

    # --- 主仮説の判定: 踏み上げ方向のうち基準を満たすものがあるか ---
    main = grid[grid["方向"] == "踏み上げ方向"].copy()
    main["合格"] = ((main["t"].abs() >= 2) & (main["PF"] >= 1.3) & (main["n"] >= 100))
    passed = main[main["合格"]]
    best = (main.loc[main["t"].abs().idxmax()] if len(main) else None)

    if len(passed):
        row = passed.loc[passed["t"].abs().idxmax()]
        key = (row["Z閾値"], row["保有日"], row["方向"])
        trades, res = detail[key]
        verdict = "合格"
        reason = (f"Z閾値±{row['Z閾値']} / 保有{row['保有日']}日 / 踏み上げ方向で "
                  f"控除後 t={row['t']:.2f} / PF={row['PF']:.2f} / n={row['n']} と共通基準を満たす。"
                  + ("" if abs(row["t"]) >= crit else
                     f" ただし Bonferroni臨界 {crit:.2f} 未達のため要追試。"))
    else:
        trades = pd.DataFrame()
        res = summarize([])
        verdict = "不合格"
        if best is None:
            reason = "シグナルが生成されなかった。"
        elif best["n"] < 100:
            reason = f"最良でも取引回数 {int(best['n'])} 回で基準の100回未満。"
        else:
            reason = (f"踏み上げ方向のどの組み合わせも基準を満たさない"
                      f"（最良 t={best['t']:.2f} / PF={best['PF']:.2f} / n={int(best['n'])}）。")

    # --- レジーム別（合格候補のみ） ---
    regime_str = "該当なし"
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        rr = []
        for name, grp in trades.groupby(lab.to_numpy()):
            r = summarize(grp["net_pips"])
            rr.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
        tables.append(("円高期 / 円安期 別（合格候補のコスト控除後）",
                       pd.DataFrame(rr, columns=["期", "n", "平均pips", "t", "PF"])
                       .to_string(index=False)))
        regime_str = " / ".join(f"{r[0]}: t={r[3]} PF={r[4]} n={r[1]}" for r in rr)

    # --- 近傍安定性: 閾値 ±0.25、保有 ±1日 ---
    neigh = "該当なし（合格候補なし）"
    if verdict == "合格":
        base_thr, base_hold = float(row["Z閾値"]), int(row["保有日"])
        st = []
        for dthr, dhold in ((-0.25, 0), (+0.25, 0), (0, -1), (0, +1)):
            thr2, hold2 = base_thr + dthr, max(1, base_hold + dhold)
            sigs = []
            for sig_date, zv in z.items():
                if abs(zv) < thr2:
                    continue
                nxt = tdays.searchsorted(pd.Timestamp(sig_date).normalize(), side="right")
                if nxt >= len(tdays) or nxt + hold2 >= len(tdays):
                    continue
                sigs.append((_dt.datetime.combine(tdays[nxt].date(), _dt.time(exec_hour, 0)),
                             _dt.datetime.combine(tdays[nxt + hold2].date(), _dt.time(exec_hour, 0)),
                             int(-np.sign(zv)), ""))
            tr = simulate(df, sigs, cost, sl_pips=SL_BY_HOLD.get(hold2, 120.0))
            r = summarize(tr["net_pips"] if len(tr) else [])
            st.append([f"Z±{thr2:.2f}/{hold2}日", r.n, round(r.mean_pips, 2), round(r.t, 3)])
        tables.append(("近傍安定性", pd.DataFrame(
            [["基準", int(row["n"]), row["平均pips"], row["t"]]] + st,
            columns=["設定", "n", "平均pips", "t"]).to_string(index=False)))
        signs = {np.sign(v) for v in [row["平均pips"]] + [s[2] for s in st] if v == v}
        neigh = "崩れない" if len(signs) == 1 else "崩れる"
        if neigh == "崩れる":
            verdict, reason = "不合格", "閾値・保有期間の近傍で符号が反転する。"

    rep = PhaseReport(
        phase="Phase 4", axis="くりっく365建玉",
        hypothesis="日本の個人投資家（逆張り）の建玉偏りが極端になると踏み上げ／投げが発生する",
        preregistered=f"買建玉比率の{ZSCORE_WINDOW}日Zスコア / 閾値 ±1.5・±2.0 のみ /"
                      f" 保有 1・5・20日のみ / 方向2通り = 検定{N_TESTS}回",
        n_tests=N_TESTS, bonferroni_crit_t=crit,
        result_after_cost=(f"t = {res.t:.3f} / PF = {res.pf:.3f} / 取引回数 = {res.n}"
                           if res.n else
                           (f"最良 t = {best['t']:.3f} / PF = {best['PF']:.3f} / n = {int(best['n'])}"
                            if best is not None else "シグナルなし")),
        regime_breakdown=regime_str, neighborhood=neigh,
        verdict=verdict, reason=reason,
        byproduct="建玉比率Zスコアの時系列（ロット調整型フィルターの素材になる）",
        tables=tables, notes=notes)
    if write:
        rep.write("phase4_click365")
    return rep
