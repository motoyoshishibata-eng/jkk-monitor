"""USDJPY 新ルール探索 — 実行エントリポイント。

使い方（プログラマでなくても、この4つだけ覚えれば動きます）:

  # 0. データを読めているか確認する（最初に必ず）
  python3 -m usdjpy_research.cli check --bars data/USDJPY_M1.csv

  # 1. Phase 0（DST前提の検証 + スプレッド実測）
  python3 -m usdjpy_research.cli phase0 --bars data/USDJPY_M1.csv

  # 2. Phase 1 から順に実行（合格が出たらそこで止まる）
  python3 -m usdjpy_research.cli explore --bars data/USDJPY_M1.csv

  # 3. 特定の Phase だけ
  python3 -m usdjpy_research.cli phase 3 --bars data/USDJPY_M1.csv

  # 4. AVA のコストで再計算（§10-2 ブローカー差分チェック）
  python3 -m usdjpy_research.cli phase 1 --bars data/USDJPY_M1.csv --broker ava

結果は usdjpy_research/reports/ に Phase ごとの .md として保存され、
PROJECT.md の「Phase 結果」セクションにも自動で追記されます。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common.cost import load_broker, CONFIG_DIR
from .common.io import load_mt5_bars, describe_bars
from .common import regime
from .phases import (phase0_dst, phase0_spread, phase1_fomc_cycle, phase2_pre_fomc,
                     phase3_ny_cut, phase4_click365, phase5_rebalance,
                     phase6_quarter_end)

PHASES = {
    1: ("FOMCサイクル時間", phase1_fomc_cycle),
    2: ("プレFOMCドリフト", phase2_pre_fomc),
    3: ("NYオプションカット後のレジーム変化", phase3_ny_cut),
    4: ("くりっく365建玉", phase4_click365),
    5: ("月末リバランス量推定（UBS修正版）", phase5_rebalance),
    6: ("四半期末のドル調達需給", phase6_quarter_end),
}


def _banner(msg: str) -> None:
    print("\n" + "=" * 72 + f"\n{msg}\n" + "=" * 72)


def _preflight(broker: str) -> bool:
    """実行前チェック。落ちているものがあれば理由を出して False。"""
    ok = True
    if not (CONFIG_DIR / f"spread_{broker}.csv").exists():
        print(f"[NG] config/spread_{broker}.csv が無い。"
              f"先に `phase0` を実行してスプレッドを実測してください。\n"
              f"     固定値でのバックテストは指示書 §0-3 で禁止されています。")
        ok = False
    if regime.is_placeholder():
        print("[警告] 円高期/円安期の区分が暫定値です。"
              "config/regimes.csv を PROJECT.md §4 決定表の値に書き換えてください。"
              "（実行は続行しますが、期別集計は参考値扱いになります）")
    return ok


def cmd_check(a) -> int:
    df = load_mt5_bars(a.bars)
    _banner("データ読み込み結果（必ず目で確認してください）")
    print(describe_bars(df))
    print("\n確認ポイント:")
    print("  - period が 2007年起点になっているか（指示書 §1）")
    print("  - bar step が想定の時間足か（Phase 2・3 は M1 が必要）")
    print("  - spread の中央値が 0 でないか（0 なら Phase 0-3 が実測できない）")
    print("  - bars/year が極端に少ない年が無いか（欠損期間は結果を歪めます）")
    return 0


def cmd_phase0(a) -> int:
    _banner("Phase 0-2: サーバー時間 / DST 前提の実データ検証")
    r = phase0_dst.run(a.bars)
    print(r.to_markdown())
    _banner(f"Phase 0-3: コスト実測（{a.broker}）")
    r2 = phase0_spread.run(a.bars, broker=a.broker)
    print(r2.to_markdown())
    if r.verdict != "前提は成立":
        print("\n!! DST前提が崩れています。Phase 1 以降に進む前に必ず報告・確認してください。")
        return 1
    return 0


def _run_one(n: int, a) -> object:
    name, mod = PHASES[n]
    _banner(f"Phase {n}: {name}")
    cost = load_broker(a.broker)
    rep = mod.run(a.bars, cost)
    print(rep.to_markdown())
    return rep


def cmd_phase(a) -> int:
    if a.number not in PHASES:
        print(f"Phase {a.number} は存在しません。1〜6 を指定してください。")
        return 2
    if not _preflight(a.broker):
        return 1
    rep = _run_one(a.number, a)
    return 0 if rep.verdict in ("合格", "前提は成立", "完了") else 0


def cmd_explore(a) -> int:
    """Phase 1 から順に実行し、合格が出た時点で止める（指示書 §0 の方針）。"""
    if not _preflight(a.broker):
        return 1
    log = []
    for n in sorted(PHASES):
        rep = _run_one(n, a)
        log.append((n, rep.verdict, rep.reason))
        if rep.verdict == "合格":
            _banner(f"Phase {n} で合格。探索を停止し、§10 の合格後の流れに進んでください。")
            break
        print(f"\nPhase {n}: {PHASES[n][0]} — {rep.verdict}\n"
              f"理由: {rep.reason}\n残った副産物: {rep.byproduct}\n")
    _banner("探索サマリ")
    for n, v, why in log:
        print(f"  Phase {n} {PHASES[n][0]:<28} {v:<6} {why}")
    if all(v != "合格" for _, v, _ in log):
        print("\n全Phase不合格。指示書 §9 の Phase 7（予備軸）へ進んでください:")
        print("  1. 米国債の償還・利払いカレンダー（ゴトー日効果との交絡分離が必須）")
        print("  2. フェアバリュー残差の平均回帰（日米2年金利差・日経平均・原油）")
        print("  3. 東京/ロンドン/NY のボラ配分の日次逸脱")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="usdjpy_research",
        description="USDJPY 新ルール探索 検証指示書 v1 の実行環境",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    # --bars / --broker はサブコマンドの前でも後でも書けるようにしておく
    # （オプションの位置で怒られるのは、この手のツールで一番よくある詰まり方なので）
    # サブコマンド側は default=SUPPRESS にして、指定が無ければ
    # 親側で解釈した値をそのまま残す（後勝ちで上書きされないように）
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--bars", default=argparse.SUPPRESS,
                        help="MT5 からエクスポートしたバーCSV")
    common.add_argument("--broker", default=argparse.SUPPRESS,
                        help="コスト表の名前（finest / ava）。config/spread_<name>.csv を読む")
    p.add_argument("--bars", default="usdjpy_research/data/USDJPY_M1.csv",
                   help=argparse.SUPPRESS)
    p.add_argument("--broker", default="finest", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", parents=[common],
                   help="データを正しく読めているか確認する").set_defaults(fn=cmd_check)
    sub.add_parser("phase0", parents=[common],
                   help="Phase 0（DST検証 + スプレッド実測）").set_defaults(fn=cmd_phase0)
    sp = sub.add_parser("phase", parents=[common], help="特定の Phase を実行")
    sp.add_argument("number", type=int)
    sp.set_defaults(fn=cmd_phase)
    sub.add_parser("explore", parents=[common],
                   help="Phase 1 から順に実行（合格で停止）").set_defaults(fn=cmd_explore)

    a = p.parse_args(argv)
    if not Path(a.bars).exists():
        print(f"バーCSVが見つかりません: {a.bars}\n"
              f"MT5 の 表示->銘柄(Ctrl+U)->USDJPY->バー から M1 をエクスポートして"
              f"置いてください。")
        return 2
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
