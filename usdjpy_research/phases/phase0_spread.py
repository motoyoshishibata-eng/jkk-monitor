"""Phase 0-3: 時間帯別スプレッドの実測。

MT5 のバーCSVには <SPREAD> 列（point 単位）が入っていることが多い。
これをサーバー時間の1時間刻みで集計し config/spread_<broker>.csv を作る。

<SPREAD> 列が無い / 全部 0 のときは **固定値で代用せず失敗させる**。
指示書 §0-3 が固定値・最小値でのバックテストを明確に禁止しているため。
その場合の代替手段はレポート末尾に出力する。

AVA 側は同じ形式の CSV を手で用意する（§10-2 のブローカー差分チェック用）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..common.io import load_mt5_bars, infer_point_size, PIP
from ..common.report import PhaseReport
from ..common.cost import CONFIG_DIR

FALLBACK_HELP = """\
<SPREAD> 列が使えないときの代替手段（どれか1つ）:
  1. MT5 の「ティックチャート」や板情報を平日1〜2週間 記録し、時間帯別に平均を取る
  2. ファイネストの公表スプレッド（原則固定でない時間帯を含む）を時間帯別に手入力
  3. MT5 のストラテジーテスターで「実際のティックに基づく全ティック」を使い、
     ティックデータの ask-bid から集計する
いずれの場合も config/spread_finest.csv を
  hour,spread_pips,n,p90_pips
の形式で手で作れば以降の Phase はそのまま動く。
"""


def run(bars_path: str, broker: str = "finest", write: bool = True) -> PhaseReport:
    df = load_mt5_bars(bars_path)
    pt = infer_point_size(df)
    notes, tables = [], []
    ok = False
    out_path = CONFIG_DIR / f"spread_{broker}.csv"

    if "spread" not in df.columns:
        notes.append("CSV に <SPREAD> 列が無い。エクスポート時に含めるか、代替手段を使うこと。")
    else:
        sp_pips = df["spread"].astype("float64") * pt / PIP
        valid = sp_pips[(sp_pips > 0) & np.isfinite(sp_pips)]
        if valid.empty:
            notes.append("<SPREAD> 列が全て 0（ヒストリカルバーでは 0 埋めされることが多い）。")
        else:
            g = valid.groupby(valid.index.hour)
            table = pd.DataFrame({
                "hour": sorted(g.groups),
                "spread_pips": g.mean().round(3).reindex(range(24)).to_numpy(),
                "median_pips": g.median().round(3).reindex(range(24)).to_numpy(),
                "p90_pips": g.quantile(0.90).round(3).reindex(range(24)).to_numpy(),
                "n": g.size().reindex(range(24)).fillna(0).astype(int).to_numpy(),
            })
            # 欠損時間帯は全体中央値で埋める（0 埋めして無コスト扱いにしない）
            fill = float(valid.median())
            table["spread_pips"] = table["spread_pips"].fillna(fill)
            table["median_pips"] = table["median_pips"].fillna(fill)
            table["p90_pips"] = table["p90_pips"].fillna(fill)

            # 年別も出す（古い期間は参考値なので §0-4）
            yearly = valid.groupby(valid.index.year).mean().round(3)

            if write:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                table.to_csv(out_path, index=False)
            ok = True
            tables.append((f"時間帯別スプレッド（サーバー時間, pips, {broker}）",
                           table.to_string(index=False)))
            tables.append(("年別 平均スプレッド（古い期間は参考値: 指示書 §0-4）",
                           yearly.to_string()))
            key = {"東京 (02-08)": range(2, 9), "ロンドン (09-16)": range(9, 17),
                   "NY (17-23)": range(17, 24), "早朝 (00-01)": range(0, 2)}
            summary = "\n".join(
                f"{k:<16} 平均 {table.loc[table.hour.isin(v),'spread_pips'].mean():.3f} pips"
                f"  / 90%点 {table.loc[table.hour.isin(v),'p90_pips'].mean():.3f} pips"
                for k, v in key.items())
            tables.append(("セッション別 要約", summary))
            notes.append(f"書き出し: {out_path}")
            notes.append("Phase 2（FOMC前）は 20:00〜21:00 台、Phase 3（NYカット）は "
                         "16:00〜18:00 台の値がそのまま効くので、その帯の n が"
                         "十分かを必ず確認すること。")

    if not ok:
        notes.append(FALLBACK_HELP)

    rep = PhaseReport(
        phase="Phase 0-3", axis="コスト実測（時間帯別スプレッド）",
        hypothesis="スプレッドは時間帯で大きく変わるので、固定値では優位性の判定を誤る",
        preregistered="サーバー時間の1時間刻みで平均・中央値・90%点を集計",
        n_tests=0,
        result_after_cost="該当なし（コスト表そのものを作る工程）",
        neighborhood="該当なし",
        verdict="完了" if ok else "未完了（実測データが取れていない）",
        reason=(f"config/spread_{broker}.csv を生成。以降の全 Phase がこれを参照する。"
                if ok else "スプレッドを実測できていないため、以降の Phase は実行禁止。"),
        byproduct=f"spread_{broker}.csv",
        tables=tables, notes=notes)
    if write:
        rep.write(f"phase0_spread_{broker}")
    return rep
