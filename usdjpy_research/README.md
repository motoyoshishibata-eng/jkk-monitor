# USDJPY 新ルール探索

「USDJPY 新ルール探索 検証指示書 v1」を、手元で実行できる形にしたものです。
Phase 0〜6 の検証コード・事前登録（[PROJECT.md](PROJECT.md)）・
指示書 §11 形式のレポート出力が入っています。

> **重要**: この作業を行った環境は外部ネットワークが GitHub と PyPI にしか
> 出られず、価格データ・FOMC日程・くりっく365建玉・株価指数のいずれも
> 取得できませんでした。そのため **数値結果は一つも出していません**。
> 合否・t値・PF は、あなたの手元でデータを置いて実行したときに初めて出ます。

## 使い方

MT5 が入っている Windows 側で、このフォルダを持ってきて実行してください。

```bash
pip install pandas numpy

# 0) まずデータを読めているか確認する（必ず最初に）
python3 -m usdjpy_research.cli check --bars usdjpy_research/data/USDJPY_M1.csv

# 1) Phase 0: DST前提の検証 + 時間帯別スプレッドの実測
python3 -m usdjpy_research.cli phase0 --bars usdjpy_research/data/USDJPY_M1.csv

# 2) Phase 1 から順に実行（合格が出たらそこで自動停止）
python3 -m usdjpy_research.cli explore --bars usdjpy_research/data/USDJPY_M1.csv

# 3) 特定の Phase だけ
python3 -m usdjpy_research.cli phase 4 --bars usdjpy_research/data/USDJPY_M1.csv

# 4) AVA のコストで再計算（指示書 §10-2 のブローカー差分チェック）
python3 -m usdjpy_research.cli phase 1 --bars usdjpy_research/data/USDJPY_M1.csv --broker ava
```

結果は `reports/phaseN_*.md` に保存され、`PROJECT.md` の「Phase 結果」にも
自動で追記されます（同じ Phase を再実行すると差し替わります）。

データの置き方は [data/README.md](data/README.md) を見てください。

## 実行する前に必ず直すもの

| ファイル | 何が問題か |
|---|---|
| `config/regimes.csv` | 円高期／円安期の区分が**暫定値**。PROJECT.md §4 決定表の区分に書き換える。書き換えるまで全レポート先頭に警告が出ます |
| `config/fomc_dates.csv` | 全165件が `verified=no`。federalreserve.gov と突き合わせて `yes` にする。1日ずれるだけで Phase 1・2 の結果は変わります |
| `config/intervention_days.csv` | 財務省「外国為替平衡操作の実施状況」と突き合わせる |

## 指示書の禁止事項をコードで担保している箇所

- **固定スプレッド禁止** — `common/cost.py: load_broker()` は実測ファイルが無いと
  例外を投げて止まります。黙って固定値に落ちません。
- **SL必須** — `common/backtest.py: simulate()` は `sl_pips=None` を
  明示フラグ無しに受け付けません。
- **事前登録した閾値の固定** — Phase 1・3・4・5 の窓と閾値はモジュール定数で、
  `tests/test_core.py::test_preregistered_constants_unchanged` が変更を検出します。
- **先読み防止** — Zスコアは当日を含まない過去N日基準
  (`test_zscore_has_no_lookahead`)、くりっく365は日報更新を待って翌営業日に約定。
- **多重比較の記録** — 各 Phase が検定回数を数え、名目 \|t\|=2 と
  Bonferroni 臨界値を必ず併記します。
- **週末の穴で約定させない** — 6時間以上の欠損を跨ぐエントリーは取引不成立扱い
  (`test_simulate_skips_trade_across_a_data_gap`)。

## 構成

```
usdjpy_research/
  PROJECT.md        事前登録（Phase 1〜6）+ 結果の追記先
  cli.py            実行エントリポイント
  common/
    tz.py           サーバー時間・米国/英国DST・各イベントのサーバー時刻
    io.py           MT5 CSV の読み込み（区切り・列名を自動判別）
    cost.py         時間帯別スプレッド / スワップ / ロールオーバー回数
    backtest.py     約定・SL・コスト控除の共通シミュレータ
    stats.py        t値 / PF / Bonferroni / Newey-West / Zスコア
    events.py       FOMC・日銀日程とサイクル日ラベリング
    regime.py       円高期／円安期
    report.py       指示書 §11 形式の出力
  phases/           phase0_dst, phase0_spread, phase1〜phase6
  config/           fomc_dates / regimes / intervention_days / spread_*
  data/             （Git対象外）ここに CSV を置く
  reports/          （Git対象外）Phase ごとの結果
  tests/            29件のユニットテスト
  tools/            make_synthetic.py（動作確認用の合成データ）
```

## テスト

```bash
pip install pytest && python3 -m pytest usdjpy_research/tests/ -q
```

相場の結果ではなく「計算が合っているか」だけを見ています。
スワップの水曜3倍、SLの不利側スリップ、Zスコアの先読み防止、
FOMCサイクル日のラベリング、事前登録した定数が動いていないこと、など。

---

*本ディレクトリは自己のEA開発のための検証計画であり、投資助言ではない。*
