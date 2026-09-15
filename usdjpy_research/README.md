# USDJPY 新ルール探索

「USDJPY 新ルール探索 検証指示書 **v1.1**」を、手元で実行できる形にしたものです。
Phase 0〜6 の検証コード・事前登録（[PREREGISTRATION.md](PREREGISTRATION.md)）・
指示書 §11 形式のレポート出力が入っています。

> ファイル名が `PROJECT.md` でないのは、既存EA開発リポジトリの `PROJECT.md`
> （§4決定表・CFTC検証結果・TASK_37切替ルール等）が唯一の真実の源であるべきで、
> 同名ファイルが2つあると参照先を見失うためです（v1.1 差分5）。

> **重要**: この作業を行った環境は外部ネットワークが GitHub と PyPI にしか
> 出られず、価格データ・くりっく365建玉・株価指数は取得できませんでした。
> そのため **数値結果は一つも出していません**。
> 合否・p値・PF は、あなたの手元でデータを置いて実行したときに初めて出ます。
> FOMC日程だけは 2021〜2027年分を提供いただいたので取り込み済みです
> （2007〜2020年分は未取得）。

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

結果は `reports/phaseN_*.md` に保存され、`PREREGISTRATION.md` の「Phase 結果」にも
自動で追記されます（同じ Phase を再実行すると差し替わります）。

Phase 6 のプール検定を有効にするには `data/EURUSD_M1.csv` / `GBPUSD_M1.csv` /
`AUDUSD_M1.csv` も同じ形式で置いてください（無ければスキップされ、その旨が
レポートに残ります）。

データの置き方は [data/README.md](data/README.md) を見てください。

## 実行する前に必ず直すもの

| ファイル | 何が問題か |
|---|---|
| `config/regimes.csv` | 円高期／円安期の区分が**暫定値**。**既存EA開発リポジトリの** `PROJECT.md` §4 決定表の区分に書き換える。書き換えるまで全レポート先頭に警告が出ます |
| `config/fomc_dates.csv` | 2021〜2027年（56件）は突合済み。**2007〜2020年（117件）が `verified=no`**。federalreserve.gov/monetarypolicy/fomc_historical.htm の年別ページと突き合わせる。1日ずれるだけで Phase 1・2 の結果は変わります |
| 同上・`press_conference` 列 | 2011年4月〜2018年が `unknown`。埋めると Phase 2 のサンプルが15件増え、差分3の記者会見別分割が実質的に効くようになります |
| `config/intervention_days.csv` | 財務省「外国為替平衡操作の実施状況」と突き合わせる |

### FOMC 発表時刻は年間固定ではありません（v1.1 差分2）

| 時期 | 発表時刻(ET) | サーバー時刻 |
|---|---|---|
| 2007 〜 2011年3月 | 14:15 | **21:15** |
| 2011年4月 〜 2013年3月12日・記者会見なし | 14:15 | 21:15 |
| 2011年4月 〜 2013年3月12日・記者会見あり/不明 | 要確認 | **時刻不明 → 自動除外** |
| 2013年3月13日 〜 現在 | 14:00 | **21:00** |

`events.announcement_server_time(date, press_conference)` が日付ごとに引きます。
定数直書きはしていません。除外件数は Phase 2 のレポートに残ります。

## 指示書の禁止事項をコードで担保している箇所

- **固定スプレッド禁止** — `common/cost.py: load_broker()` は実測ファイルが無いと
  例外を投げて止まります。黙って固定値に落ちません。
- **SL必須** — `common/backtest.py: simulate()` は `sl_pips=None` を
  明示フラグ無しに受け付けません。
- **事前登録した閾値の固定** — Phase 1・3・4・5 の窓と閾値はモジュール定数で、
  `tests/test_core.py::test_preregistered_constants_unchanged` が変更を検出します。
- **先読み防止** — Zスコアは当日を含まない過去N日基準
  (`test_zscore_has_no_lookahead`)、くりっく365は日報更新を待って翌営業日に約定。
- **判定方法を仮説の形に合わせる（v1.1 差分4）** — イベントスタディ
  （Phase 1・2・3・5・6）は **パーミュテーション検定**（10,000回・曜日と月を層化・
  片側2.5%以内）+ PF≥1.3。総当たり探索型（Phase 4 の12通りの格子）は従来どおり
  n≥100 + \|t\|≥2 + PF≥1.3 + Bonferroni補正。
- **レジーム別掲の扱い** — n≥30 は「参考値」ラベル付きで単独判定には使わず、
  n<30 は「n不足のため算出せず」と出します（`test_regime_cell_thresholds`）。
- **プール検定でペア間の相関を保つ** — Phase 6 の EURUSD/GBPUSD/AUDUSD プールは、
  1回の置換で全ペアに同じランダム日付を使います。ペアごとに別の日を引くと
  帰無分布の分散が過小になり有意に見えすぎるため
  (`test_pooled_permutation_uses_same_dates_across_pairs`)。
- **週末の穴で約定させない** — 6時間以上の欠損を跨ぐエントリーは取引不成立扱い
  (`test_simulate_skips_trade_across_a_data_gap`)。

## 構成

```
usdjpy_research/
  PREREGISTRATION.md 事前登録（Phase 1〜6）+ 結果の追記先
  cli.py            実行エントリポイント
  common/
    tz.py           サーバー時間・米国/英国DST・各イベントのサーバー時刻
    io.py           MT5 CSV の読み込み（区切り・列名を自動判別）
    cost.py         時間帯別スプレッド / スワップ / ロールオーバー回数
    backtest.py     約定・SL・コスト控除の共通シミュレータ
    stats.py        t値 / PF / Bonferroni / Newey-West / Zスコア /
                    パーミュテーション検定（単一・複数窓・プール）
    events.py       FOMC・日銀日程、発表時刻の時期テーブル、サイクル日ラベリング
    regime.py       円高期／円安期
    report.py       指示書 §11 形式の出力
  phases/           phase0_dst, phase0_spread, phase1〜phase6
  config/           fomc_dates / regimes / intervention_days / spread_*
  data/             （Git対象外）ここに CSV を置く
  reports/          （Git対象外）Phase ごとの結果
  tests/            59件のユニットテスト
  tools/            make_synthetic.py（動作確認用の合成データ）
```

## テスト

```bash
pip install pytest && python3 -m pytest usdjpy_research/tests/ -q
```

相場の結果ではなく「計算が合っているか」だけを見ています。
スワップの水曜3倍、SLの不利側スリップ、Zスコアの先読み防止、
FOMCサイクル日のラベリング、発表時刻の時期テーブル、
パーミュテーション検定が帰無では有意にならず対立では有意になること、
ロンドンFIXの英米DSTギャップ、事前登録した定数が動いていないこと、など。

---

*本ディレクトリは自己のEA開発のための検証計画であり、投資助言ではない。*
