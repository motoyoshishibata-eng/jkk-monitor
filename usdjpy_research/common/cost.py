"""コストモデル（スプレッド・スワップ・スリッページ）。

指示書 §0-3 / §1 より:
  - スプレッドは **時間帯別の実測平均** を使う。固定値・最小値は禁止。
  - 合格判定はコスト控除後の数値のみで行う。
  - 最終的に AVA で動かすので、AVA のコストでも再計算できる形にしておく。

スプレッド表は phases/phase0_spread.py が生成する
config/spread_<broker>.csv（hour,spread_pips,...）を読む。
AVA 側は自動計測できないので、MT5 の気配やレポートから手入力する。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@dataclass
class CostModel:
    """1ブローカー分のコスト設定。

    spread_by_hour : サーバー時間の「時」-> 平均スプレッド(pips)
    swap_long/short: 1泊あたりの pips（受取が +、支払が -）
    slippage_pips  : 片道あたりの想定スリッページ
    commission_pips: 往復手数料の pips 換算（ファイネスト/AVA は通常 0）
    """

    name: str
    spread_by_hour: dict[int, float] = field(default_factory=dict)
    default_spread_pips: float = 1.0
    swap_long: float = 0.0
    swap_short: float = 0.0
    slippage_pips: float = 0.0
    commission_pips: float = 0.0

    # ---- 読み書き ----
    @classmethod
    def from_csv(cls, path: str | Path, name: str | None = None, **kw) -> "CostModel":
        df = pd.read_csv(path)
        df.columns = [c.strip().lower() for c in df.columns]
        col = "spread_pips" if "spread_pips" in df.columns else df.columns[1]
        table = {int(h): float(v) for h, v in zip(df["hour"], df[col])
                 if pd.notna(v)}
        return cls(name=name or Path(path).stem, spread_by_hour=table, **kw)

    @classmethod
    def flat(cls, name: str, pips: float, **kw) -> "CostModel":
        """検算・感度分析用の固定スプレッド。本番判定には使わないこと。"""
        return cls(name=name, spread_by_hour={h: pips for h in range(24)},
                   default_spread_pips=pips, **kw)

    # ---- コスト計算 ----
    def spread(self, dt: _dt.datetime) -> float:
        return self.spread_by_hour.get(dt.hour, self.default_spread_pips)

    def entry_exit_cost(self, entry_dt: _dt.datetime,
                        exit_dt: _dt.datetime | None = None) -> float:
        """往復の取引コスト(pips, 常に正)。

        バー値は bid 基準なので、買いは entry で ask を払い exit は bid。
        つまりスプレッドは往復で 1 回分だけ計上する（エントリー時刻の実測値）。
        スリッページは往復2回分、手数料はそのまま往復分。
        """
        return (self.spread(entry_dt)
                + 2 * self.slippage_pips
                + self.commission_pips)

    def swap_cost(self, entry_dt: _dt.datetime, exit_dt: _dt.datetime,
                  direction: int) -> float:
        """保有中のスワップ損益(pips)。受取なら +、支払なら -。"""
        rate = self.swap_long if direction > 0 else self.swap_short
        return rate * count_rollovers(entry_dt, exit_dt)

    def pnl_after_cost(self, gross_pips: float, entry_dt: _dt.datetime,
                       exit_dt: _dt.datetime, direction: int) -> float:
        return (gross_pips
                - self.entry_exit_cost(entry_dt, exit_dt)
                + self.swap_cost(entry_dt, exit_dt, direction))


def count_rollovers(entry_dt: _dt.datetime, exit_dt: _dt.datetime) -> int:
    """跨いだロールオーバー回数。水曜跨ぎは3倍、土日は加算しない。

    サーバー時間 24:00(=翌0:00) をロールオーバー時刻とみなす簡略モデル。
    """
    if exit_dt <= entry_dt:
        return 0
    n = 0
    d = entry_dt.date()
    end = exit_dt.date()
    while d < end:
        nxt = d + _dt.timedelta(days=1)
        wd = d.weekday()          # 0=月 ... 6=日
        if wd == 2:               # 水曜 -> 木曜 は 3日分
            n += 3
        elif wd in (4, 5):        # 金->土, 土->日 は付かない
            n += 0
        else:
            n += 1
        d = nxt
    return n


def load_broker(name: str, **kw) -> CostModel:
    """config/spread_<name>.csv と config/swap_<name>.csv を読む。

    ファイルが無ければ「未計測」として例外にする。無言で固定値に
    フォールバックすると指示書 §0-3 の禁止事項を静かに破ることになるため。
    """
    sp = CONFIG_DIR / f"spread_{name}.csv"
    if not sp.exists():
        raise FileNotFoundError(
            f"{sp} が無い。先に Phase 0-3（phase0_spread.py）で "
            f"{name} のスプレッドを実測してください。"
            " 固定値でのバックテストは指示書で禁止されています。")
    swap_kw = dict(kw)
    sw = CONFIG_DIR / f"swap_{name}.csv"
    if sw.exists():
        s = pd.read_csv(sw)
        s.columns = [c.strip().lower() for c in s.columns]
        row = s.iloc[0]
        swap_kw.setdefault("swap_long", float(row.get("swap_long_pips", 0.0)))
        swap_kw.setdefault("swap_short", float(row.get("swap_short_pips", 0.0)))
    return CostModel.from_csv(sp, name=name, **swap_kw)
