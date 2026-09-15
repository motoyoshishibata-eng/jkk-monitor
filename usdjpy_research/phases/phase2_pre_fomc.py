"""Phase 2: プレFOMCドリフト（発表「前」24時間）。

事前登録:
  仮説   : FOMC発表前24時間に USDJPY が一方向に偏る。
           方向は事前に決め打ちできない（為替では見解が割れている）ため、
           **ロング方向で集計し両側で判定する**。ショートが有意なら符号が負で出る。
  観測窓 : 前営業日の発表時刻 → 当日の発表時刻 −5分。
           **発表時刻は年間固定ではない**（v1.1 差分2）。時期テーブルで引く:
             2013-03-13 以降        -> 14:00 ET = サーバー 21:00
             2011-04-01 より前       -> 14:15 ET = サーバー 21:15
             2011-04-01〜2013-03-12  -> 記者会見なし会合のみ 21:15、
                                        会見あり/不明は **時刻不明 -> サンプルから除外**
           内訳は3分割: 発表時刻→翌02:00 / 02:00→09:00 / 09:00→発表5分前。
  判定   : 事前登録された単一仮説のイベントスタディなので、
           **パーミュテーション検定**（10,000回・曜日月を層化・片側2.5%以内）
           + PF>=1.3（コスト控除後）で判定する（v1.1 差分4）。
           n>=100 は課さない（定例FOMCは年8回で原理的に届かないため）。
  必須分割（v1.1 差分3）: 記者会見あり / なし / 全会合 の3群を必ず別掲する。
           NY連銀の追試では、新しいサンプルで大きな超過リターンが残るのは
           **記者会見のある会合のみ**で、会見なし会合では証拠が見られない。
           全部混ぜた集計だけを出すのは不可。
           SEP公表会合かどうかも副次的に記録する（判定には使わない）。

安全上の制約（指示書 §4 の警告）:
  - 発表の瞬間をまたぐ保有は設計から外す。必ず発表5分前に決済する。
  - SL は広め。SL到達時はスリッページを加算した悲観シナリオでも集計する
    （0 / 3 / 5 pips の3本を必ず併記）。
  - 日銀会合と近接する週は別掲する。
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
from ..common.stats import (summarize, permutation_test, judge, regime_cell,
                            PERM_ITERS, TestCounter)

# --- 事前登録した定数（実行後に変更禁止） ---
EXIT_OFFSET_MIN = 5               # 発表の何分前に決済するか
SUB_SPLITS = ((2, 0), (9, 0))     # 内訳3分割の境目（サーバー時間）
DEFAULT_SL_PIPS = 80.0            # 通常戦略より広め（指示書 §4）
SLIPPAGE_SCENARIOS = (0.0, 3.0, 5.0)
BOJ_PROXIMITY_DAYS = 3
N_TESTS = 6                       # 全窓1 + 内訳3 + レジーム別2（参考）


def _prev_trading_day(tdays: pd.DatetimeIndex, d: pd.Timestamp):
    pos = tdays.searchsorted(pd.Timestamp(d).normalize(), side="left") - 1
    return tdays[pos] if pos >= 0 else None


def _window(tdays, day, ann: _dt.time):
    """(エントリー時刻, 決済時刻)。発表時刻の24時間前 -> 発表5分前。"""
    prev = _prev_trading_day(tdays, day)
    if prev is None:
        return None
    t0 = _dt.datetime.combine(prev.date(), ann)
    exit_t = (_dt.datetime.combine(day.date(), ann)
              - _dt.timedelta(minutes=EXIT_OFFSET_MIN))
    return (t0, exit_t) if exit_t > t0 else None


def _fmt(r) -> str:
    return (f"n={r.n:>4}  平均={r.mean_pips:>7.2f}pips  t={r.t:>6.3f}  "
            f"PF={r.pf:>6.3f}  勝率={r.win_rate*100:>5.1f}%")


def run(bars_path: str, cost: CostModel, *, sl_pips: float = DEFAULT_SL_PIPS,
        n_iter: int = PERM_ITERS, write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
    tdays = events.trading_day_index(df.index)
    fomc = events.load_fomc(scheduled_only=True)
    fomc = fomc[(fomc["date"] >= tdays.min()) & (fomc["date"] <= tdays.max())].copy()
    _, audit_msgs = events.audit_fomc()
    boj = events.load_boj()

    counter = TestCounter()
    tables, notes = [], [b for b in [regime.warning_banner().strip()] if b]
    notes += [f"FOMC日程チェック: {m}" for m in audit_msgs]

    # --- 差分2: 発表時刻が確定できない会合を除外し、件数を残す ---
    n_all = len(fomc)
    excluded = fomc[~fomc["time_known"]]
    fomc = fomc[fomc["time_known"]].copy()
    tables.append(("発表時刻の時期区分と除外（v1.1 差分2）", "\n".join([
        f"データ期間内の定例会合      : {n_all} 件",
        f"発表時刻が確定した会合      : {len(fomc)} 件",
        f"時刻不明のため除外した会合  : {len(excluded)} 件"
        + (f"（{excluded['date'].min().date()} 〜 {excluded['date'].max().date()}）"
           if len(excluded) else ""),
        "",
        "時刻の内訳（サーバー時間）:",
        *(f"  {t} ... {n} 件"
          for t, n in fomc["announcement_server_time"].astype(str).value_counts().items()),
        "",
        "除外は『2011年4月〜2013年3月の記者会見あり会合の発表時刻が未確認』であるため。",
        "config/fomc_dates.csv の press_conference 列を埋めれば、"
        "会見なし会合（21:15）が復活してサンプルが増えます。推測で埋めないこと。",
    ])))

    if fomc.empty:
        rep = PhaseReport(phase="Phase 2", axis="プレFOMCドリフト（発表前24時間）",
                          verdict="未実行", reason="発表時刻が確定した会合が0件。",
                          tables=tables, notes=notes)
        if write:
            rep.write("phase2_pre_fomc")
        return rep

    ann_time = dict(zip(fomc["date"], fomc["announcement_server_time"]))

    # --- 主検定用シグナル ---
    sig, used_dates = [], []
    for d in fomc["date"]:
        w = _window(tdays, d, ann_time[d])
        if w:
            sig.append((w[0], w[1], +1, str(d.date())))
            used_dates.append(d)

    by_slip = {}
    for slip in SLIPPAGE_SCENARIOS:
        tr = simulate(df, sig, cost, sl_pips=sl_pips, sl_slippage_pips=slip)
        by_slip[slip] = (tr, summarize(tr["net_pips"] if len(tr) else []))
    counter.count("24時間窓")
    trades, res = by_slip[SLIPPAGE_SCENARIOS[0]]
    gross = summarize(trades["gross_pips"] if len(trades) else [])
    worst_tr, worst = by_slip[SLIPPAGE_SCENARIOS[-1]]

    tables.append(("主検定: 前営業日の発表時刻 → 発表5分前（ロング方向・両側）", "\n".join(
        [f"控除前         : {_fmt(gross)}"]
        + [f"控除後 slip{int(s)}pips: {_fmt(by_slip[s][1])}" for s in SLIPPAGE_SCENARIOS]
        + [f"SL到達率       : {trades['hit_sl'].mean()*100:.1f}%" if len(trades) else "",
           "",
           "判定は『控除後 slip0』を主、『控除後 slip5』を悲観シナリオとして見る。",
           "悲観シナリオで基準割れするなら実運用では通らないと考えること。"])))

    # --- 差分4: パーミュテーション検定 ---
    # 全営業日を候補にして同じ窓の損益を作り、そこから曜日・月を揃えて再抽出する。
    # 候補日の発表時刻はその日付の時期テーブル値（会見なし基準）を使う。
    cand_sig, cand_days = [], []
    for d in tdays:
        t = events.announcement_server_time(d, "no")
        if t is None:
            continue
        w = _window(tdays, d, t)
        if w:
            cand_sig.append((w[0], w[1], +1, str(d.date())))
            cand_days.append(d)
    cand_tr = simulate(df, cand_sig, cost, sl_pips=sl_pips)
    # tag は「その取引が代表するイベント日」。エントリーは前営業日なので、
    # entry_time ではなく tag で寄与を集計する（日付の付け替えミスを避けるため）
    if len(cand_tr):
        contrib = cand_tr.groupby("tag")["net_pips"].sum()
        contrib.index = pd.DatetimeIndex(contrib.index)
        contrib = contrib.sort_index()
    else:
        contrib = pd.Series(dtype="float64", index=pd.DatetimeIndex([]))

    perm = permutation_test(contrib, pd.DatetimeIndex(used_dates), n_iter=n_iter)
    tables.append((f"パーミュテーション検定（v1.1 差分4・{n_iter:,}回）",
                   perm.summary() + "\n\n"
                   "イベント日をランダムな営業日に置き換え、曜日・月の分布を実測に"
                   "合わせて層化抽出している。FOMCは火水に偏るので、完全ランダムだと"
                   "帰無分布が歪むため。\n"
                   "候補日の発表時刻は、その日付の時期テーブル値（会見なし基準）を使用。"
                   + (f"\n注意: {perm.strata_note}" if perm.strata_note else "")))

    # --- 差分3: 記者会見の有無で必ず3群に分ける ---
    pc_map = dict(zip(fomc["date"], fomc["press_conference"].astype(str).str.lower()))
    sep_map = dict(zip(fomc["date"], fomc["sep"].astype(str).str.lower()))
    if len(trades):
        ev = pd.DatetimeIndex([pd.Timestamp(t) for t in trades["tag"]])
        pc = pd.Series([pc_map.get(d, "unknown") for d in ev], index=trades.index)
        rows = []
        for name, mask in (("記者会見あり", pc == "yes"),
                           ("記者会見なし", pc == "no"),
                           ("全会合（参考）", pd.Series(True, index=trades.index))):
            counter.count(f"会見分割 {name}")
            sub = trades.loc[mask]
            r = summarize(sub["net_pips"])
            p = (permutation_test(contrib, ev[mask.to_numpy()], n_iter=n_iter)
                 if len(sub) else None)
            rows.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3),
                         f"{p.p_one_sided:.4f}" if p else "—"])
        tables.append(("記者会見の有無による分割（v1.1 差分3・必須）",
                       pd.DataFrame(rows, columns=["区分", "n", "平均pips", "t", "PF",
                                                   "片側p"]).to_string(index=False)
                       + "\n\n2019年以降は全会合に記者会見があるため、分割が実質的に効くのは"
                         "\n2011〜2018年。ここを混ぜると効果があっても希釈されて見える。"))
        sep = pd.Series([sep_map.get(d, "unknown") for d in ev], index=trades.index)
        srows = []
        for name in ("yes", "no", "unknown"):
            sub = trades.loc[sep == name]
            if len(sub):
                r = summarize(sub["net_pips"])
                srows.append([name, r.n, round(r.mean_pips, 2), round(r.t, 3)])
        if srows:
            tables.append(("SEP公表会合かどうか（副次的な記録。判定には使わない）",
                           pd.DataFrame(srows, columns=["sep", "n", "平均pips", "t"])
                           .to_string(index=False)))

    # --- 内訳3分割 ---
    rows = []
    a_hm, b_hm = SUB_SPLITS
    for label, kind in (("発表時刻→翌02:00（NY後半〜オセアニア）", "s1"),
                        ("当日02:00→09:00（東京）", "s2"),
                        ("当日09:00→発表5分前（ロンドン〜NY前半）", "s3")):
        s = []
        for d in fomc["date"]:
            prev = _prev_trading_day(tdays, d)
            if prev is None:
                continue
            ann = ann_time[d]
            if kind == "s1":
                t0 = _dt.datetime.combine(prev.date(), ann)
                t1 = _dt.datetime.combine(d.date(), _dt.time(*a_hm))
            elif kind == "s2":
                t0 = _dt.datetime.combine(d.date(), _dt.time(*a_hm))
                t1 = _dt.datetime.combine(d.date(), _dt.time(*b_hm))
            else:
                t0 = _dt.datetime.combine(d.date(), _dt.time(*b_hm))
                t1 = (_dt.datetime.combine(d.date(), ann)
                      - _dt.timedelta(minutes=EXIT_OFFSET_MIN))
            if t1 > t0:
                s.append((t0, t1, +1, str(d.date())))
        tr = simulate(df, s, cost, sl_pips=sl_pips, sl_slippage_pips=3.0)
        counter.count(f"内訳 {label}")
        r = summarize(tr["net_pips"] if len(tr) else [])
        rows.append([label, r.n, round(r.mean_pips, 2), round(r.t, 3), round(r.pf, 3)])
    tables.append(("内訳（コスト控除後・slip3pips）",
                   pd.DataFrame(rows, columns=["窓", "n", "平均pips", "t", "PF"])
                   .to_string(index=False)))
    notes.append("内訳はどこで効いているかを見るための分解であり、"
                 "最も良い内訳だけを取り出して戦略にすると多重比較の罠に落ちる。")

    # --- レジーム別（差分4: n>=30 は参考値、n<30 は算出せず） ---
    regime_str = "取引なし"
    if len(trades):
        lab = regime.label_series(pd.DatetimeIndex(trades["entry_time"]))
        cells = []
        for name, grp in trades.groupby(lab.to_numpy()):
            cells.append(f"{name}: {regime_cell(summarize(grp['net_pips']))}")
        tables.append(("円高期 / 円安期 別（コスト控除後・slip0）",
                       "\n".join(cells)
                       + "\n\nレジーム別の数値は参考値であり、単独では合否判定に使わない"
                         "（v1.1 差分4）。n<30 の期は無理に数字を出さない。"))
        regime_str = " / ".join(cells)

    # --- 日銀会合との近接 ---
    if boj is not None and len(trades):
        bd = pd.DatetimeIndex(boj["date"]).normalize()
        ent = pd.DatetimeIndex(trades["entry_time"]).normalize()
        near = np.array([bool((np.abs((bd - e).days) <= BOJ_PROXIMITY_DAYS).any())
                         for e in ent])
        cells = [f"{nm}: {regime_cell(summarize(trades.loc[m, 'net_pips']))}"
                 for nm, m in (("日銀会合が近接", near), ("近接なし", ~near))]
        tables.append((f"日銀会合との近接（±{BOJ_PROXIMITY_DAYS}日）別", "\n".join(cells)))
    else:
        notes.append("日銀会合日程 config/boj_dates.csv が無いため、"
                     "円側イベント混入の別掲をスキップした。"
                     "日銀サイトの決定会合日程を入れて再実行すること。")

    # --- 近傍安定性: 窓を ±1時間ずらす ---
    stab = []
    for shift in (-1, +1):
        s = []
        for d in fomc["date"]:
            ann = ann_time[d]
            shifted = _dt.time((ann.hour + shift) % 24, ann.minute)
            w = _window(tdays, d, shifted)
            if w:
                s.append((w[0], w[1], +1, str(d.date())))
        tr = simulate(df, s, cost, sl_pips=sl_pips)
        r = summarize(tr["net_pips"] if len(tr) else [])
        stab.append([f"{shift:+d}時間", r.n, round(r.mean_pips, 2), round(r.t, 3)])
    tables.append(("近傍安定性（窓を±1時間ずらす）",
                   pd.DataFrame([["基準", res.n, round(res.mean_pips, 2), round(res.t, 3)]]
                                + stab, columns=["窓", "n", "平均pips", "t"])
                   .to_string(index=False)))
    signs = {np.sign(v) for v in [res.mean_pips] + [s[2] for s in stab] if v == v}
    stable = len(signs) == 1

    # --- 判定 ---
    ok, reason = judge(res, kind="event", perm=perm)
    pess_ok = worst.pf >= 1.3
    if ok and not pess_ok:
        ok, reason = False, (f"slip0 では基準を満たすが、slip5pips の悲観シナリオで "
                             f"PF={worst.pf:.2f} に落ちる。実運用では通らない。")
    if ok and not stable:
        ok, reason = False, "窓を±1時間ずらすと符号が反転する。"
    if len(excluded):
        reason += f"（発表時刻不明で {len(excluded)} 件を除外した上での結果）"

    rep = PhaseReport(
        phase="Phase 2", axis="プレFOMCドリフト（発表前24時間）",
        hypothesis="FOMC発表前24時間に USDJPY が一方向に偏る（方向は決め打ちせず両側）",
        preregistered="前営業日の発表時刻→当日の発表5分前。発表時刻は時期テーブル"
                      "（2013-03-13以降 21:00 / それ以前 21:15 / 2011-04〜2013-03 の"
                      "会見あり会合は時刻不明のため除外）/ 内訳3分割 / SL80pips /"
                      " スリッページ 0・3・5pips 併記 / 記者会見の有無で3群別掲",
        n_tests=max(N_TESTS, len(counter)),
        result_after_cost=f"片側p = {perm.p_one_sided:.4f}"
                          f"（{perm.percentile:.1f}パーセンタイル）/ "
                          f"PF = {res.pf:.3f} / 取引回数 = {res.n} / t = {res.t:.3f}"
                          f"（slip5悲観: PF = {worst.pf:.3f}）",
        judgment_method=f"パーミュテーション検定 {n_iter:,}回（曜日・月を層化）+ PF>=1.3",
        permutation=perm.summary().replace("\n", " / "),
        regime_breakdown=regime_str,
        neighborhood="崩れない" if stable else "崩れる",
        verdict="合格" if ok else "不合格", reason=reason,
        byproduct="FOMC前24時間の時間帯別リターン分解 / 記者会見あり・なしの差",
        tables=tables, notes=notes)
    if write:
        rep.write("phase2_pre_fomc")
    return rep
