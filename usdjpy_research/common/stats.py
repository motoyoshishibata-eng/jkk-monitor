"""合格判定に使う統計量。scipy が無い環境でも動くよう自前実装。

合格基準（指示書 §1）:
  |t| >= 2 / PF >= 1.3 / 取引回数 >= 100 / 近傍安定 / Bonferroni併記
すべて **コスト控除後** の系列に対して計算すること。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

T_MIN = 2.0
PF_MIN = 1.3
N_MIN = 100


def ndtri(p: float) -> float:
    """標準正規分布の逆累積分布（Acklam 近似 + Halley 1段補正）。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p は 0<p<1")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    else:
        q, r = p - 0.5, (p - 0.5) ** 2
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    e = 0.5 * math.erfc(-x / math.sqrt(2)) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def two_sided_p(t: float) -> float:
    """正規近似の両側p値。取引回数100以上が前提なのでt分布とほぼ一致する。"""
    return math.erfc(abs(t) / math.sqrt(2))


def bonferroni_t(n_tests: int, alpha: float = 0.05) -> float:
    """検定回数 n_tests のときの両側 Bonferroni 臨界t値（正規近似）。"""
    n_tests = max(1, int(n_tests))
    return abs(ndtri(alpha / (2 * n_tests)))


@dataclass
class Result:
    n: int
    mean_pips: float
    sd_pips: float
    t: float
    p: float
    pf: float
    win_rate: float
    total_pips: float
    max_dd_pips: float

    def passes(self, n_tests: int = 1, alpha: float = 0.05) -> dict:
        crit = bonferroni_t(n_tests, alpha)
        return {
            "t>=2": abs(self.t) >= T_MIN,
            "PF>=1.3": self.pf >= PF_MIN,
            "n>=100": self.n >= N_MIN,
            "bonferroni": abs(self.t) >= crit,
            "bonferroni_crit_t": round(crit, 3),
            "overall": (abs(self.t) >= T_MIN and self.pf >= PF_MIN
                        and self.n >= N_MIN),
        }

    def as_dict(self) -> dict:
        return asdict(self)


def _max_drawdown(pnl: np.ndarray) -> float:
    eq = np.cumsum(pnl)
    return float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0


def summarize(pnl_pips) -> Result:
    """1取引あたり損益(pips)の系列から合格判定用の指標を出す。

    pnl_pips は **コスト控除後** を渡すこと。
    """
    x = pd.Series(pnl_pips, dtype="float64").dropna().to_numpy()
    n = x.size
    if n == 0:
        return Result(0, float("nan"), float("nan"), float("nan"),
                      float("nan"), float("nan"), float("nan"), 0.0, 0.0)
    mean = float(x.mean())
    sd = float(x.std(ddof=1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    wins, losses = x[x > 0], x[x < 0]
    gross_loss = float(-losses.sum())
    pf = float(wins.sum() / gross_loss) if gross_loss > 0 else float("inf")
    return Result(n, mean, sd, t, two_sided_p(t) if sd > 0 else float("nan"),
                  pf, float((x > 0).mean()), float(x.sum()), _max_drawdown(x))


def newey_west_t(x, lags: int | None = None) -> float:
    """重複窓・自己相関がある系列の平均に対する HAC(Newey-West) t 値。

    Phase 1 の「5日超過リターンを毎日計算する」ような重複窓では、素の t 値は
    自己相関の分だけ過大になる。そういう集計をするときはこちらを併記する。
    """
    a = pd.Series(x, dtype="float64").dropna().to_numpy()
    n = a.size
    if n < 3:
        return float("nan")
    if lags is None:
        lags = int(math.floor(4 * (n / 100) ** (2 / 9)))
    e = a - a.mean()
    gamma0 = float(e @ e) / n
    var = gamma0
    for L in range(1, min(lags, n - 1) + 1):
        g = float(e[L:] @ e[:-L]) / n
        var += 2 * (1 - L / (lags + 1)) * g
    if var <= 0:
        return float("nan")
    return float(a.mean() / math.sqrt(var / n))


def zscore(s: pd.Series, window: int) -> pd.Series:
    """過去 window 本（当日を含まない）基準の Zスコア。先読み防止済み。"""
    prev = s.shift(1)
    mu = prev.rolling(window, min_periods=window).mean()
    sd = prev.rolling(window, min_periods=window).std(ddof=1)
    return (s - mu) / sd


class TestCounter:
    """そのPhaseで実施した検定回数を数える（多重比較の記録用）。"""

    def __init__(self) -> None:
        self.labels: list[str] = []

    def count(self, label: str) -> None:
        self.labels.append(label)

    def __len__(self) -> int:
        return len(self.labels)

    @property
    def crit_t(self) -> float:
        return bonferroni_t(len(self))
