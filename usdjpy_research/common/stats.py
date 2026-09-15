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


# =====================================================================
# 指示書 v1.1 差分4: 事前登録された単一仮説のイベントスタディは
# n>=100 + Bonferroni ではなく **パーミュテーション検定** で判定する。
#
# 100回基準は候補が数千ある総当たり探索の安全装置であって、
# 年8回しかない FOMC のようなイベントスタディには不適切だった。
# これは基準の緩和ではなく、判定方法を仮説の形に合わせる修正。
# PF>=1.3・コスト控除後・レジーム別掲の要件はそのまま維持する。
# =====================================================================

PERM_ALPHA = 0.025      # 片側2.5パーセンタイル（両側5%相当）
PERM_ITERS = 10_000


@dataclass
class PermResult:
    observed: float
    n_events: int
    n_iter: int
    p_upper: float          # 帰無分布が実測以上になる割合
    p_lower: float
    p_one_sided: float      # min(p_upper, p_lower)
    percentile: float       # 実測が帰無分布の何パーセンタイルか
    null_mean: float
    null_sd: float
    n_strata: int
    strata_note: str = ""

    @property
    def passed(self) -> bool:
        return self.p_one_sided <= PERM_ALPHA

    def summary(self) -> str:
        side = "上側" if self.p_upper <= self.p_lower else "下側"
        how = (f"イベント{self.n_events}件, {self.n_strata}層で曜日・月を実測に合わせて再抽出"
               if self.n_events else (self.strata_note or "帰無分布を直接構成"))
        return (f"実測 {self.observed:.3f} / 帰無分布 平均 {self.null_mean:.3f} "
                f"±{self.null_sd:.3f}\n"
                f"パーセンタイル {self.percentile:.2f}%  "
                f"片側p（{side}）= {self.p_one_sided:.4f}  "
                f"（{self.n_iter:,}回, {how}）\n"
                f"判定: {'合格' if self.passed else '不合格'}"
                f"（合格基準 片側 {PERM_ALPHA:.3f} 以内）")


def permutation_test_multi(contrib_by_group: dict, events, *,
                           n_iter: int = PERM_ITERS, seed: int = 20260915,
                           match_dow: bool = True, match_month: bool = True
                           ) -> PermResult:
    """複数の「窓の型」が混ざるイベントスタディ用のパーミュテーション検定。

    contrib_by_group : {グループ名: Series(index=候補営業日, value=寄与)}
                       グループは「保有5営業日」「保有4営業日」のように
                       窓の形が違うものを分ける。形の違う窓を混ぜて再抽出すると
                       帰無分布が実測と別物になるため。
    events           : [(イベント日, グループ名), ...]

    層は (曜日, 月, グループ) で切り、層内は非復元抽出。
    """
    ev = [(pd.Timestamp(d).normalize(), g) for d, g in events
          if g in contrib_by_group
          and pd.Timestamp(d).normalize() in contrib_by_group[g].index]
    if not ev:
        nan = float("nan")
        return PermResult(nan, 0, n_iter, nan, nan, nan, nan, nan, nan, 0,
                          "イベントが候補日に1件も一致しない")

    observed = float(np.mean([contrib_by_group[g].loc[d] for d, g in ev]))
    rng = np.random.default_rng(seed)
    totals = np.zeros(n_iter, dtype="float64")
    n_strata, shortfall = 0, []

    def key(ts, g):
        k = [g]
        if match_dow:
            k.append(int(ts.dayofweek))
        if match_month:
            k.append(int(ts.month))
        return tuple(k)

    want: dict = {}
    for d, g in ev:
        want.setdefault(key(d, g), []).append(g)

    for k, members in want.items():
        g = members[0]
        need = len(members)
        cand = contrib_by_group[g]
        ci = pd.DatetimeIndex(cand.index)
        m = np.ones(len(ci), dtype=bool)
        if match_dow:
            m &= ci.dayofweek.to_numpy() == k[1]
        if match_month:
            m &= ci.month.to_numpy() == k[2 if match_dow else 1]
        pool = cand.to_numpy()[m]
        n_strata += 1
        if pool.size == 0:
            shortfall.append(f"層{k}: 候補日なし")
            continue
        if pool.size < need:
            shortfall.append(f"層{k}: 候補{pool.size}件 < 必要{need}件のため復元抽出")
            pick = rng.integers(0, pool.size, size=(n_iter, need))
        else:
            r = rng.random((n_iter, pool.size))
            pick = np.argpartition(r, need - 1, axis=1)[:, :need]
        totals += pool[pick].sum(axis=1)

    null = totals / len(ev)
    p_up = (1 + int((null >= observed).sum())) / (1 + n_iter)
    p_lo = (1 + int((null <= observed).sum())) / (1 + n_iter)
    return PermResult(observed, len(ev), n_iter, p_up, p_lo, min(p_up, p_lo),
                      float((null < observed).mean() * 100),
                      float(null.mean()), float(null.std(ddof=1)), n_strata,
                      "; ".join(shortfall))


def permutation_test(contrib: pd.Series, event_dates, **kw) -> PermResult:
    """イベント日をランダムな営業日に置き換えて帰無分布を作る（単一の窓の型）。

    contrib      : index=候補営業日, value=その日をイベント日としたときの寄与
                   （コスト控除後の損益をそのまま入れる。SL 込みで構わない）
    event_dates  : 実際のイベント日

    曜日・月の分布を実測に合わせて層化抽出する（差分4の指定）。
    FOMC は火水に偏るので、完全ランダムだと帰無分布が歪んで
    「有意でないものが有意に見える」方向に倒れるため。
    層の中では非復元抽出（同じ日を1回の置換内で二度使わない）。
    """
    c = pd.Series(contrib).dropna().astype("float64")
    c.index = pd.DatetimeIndex(c.index).normalize()
    c = c[~c.index.duplicated()].sort_index()
    ev = [(d, "_") for d in pd.DatetimeIndex(event_dates).normalize()]
    return permutation_test_multi({"_": c}, ev, **kw)


def permutation_from_values(observed: float, null_values) -> PermResult:
    """すでに帰無分布を作ってある場合のラッパー（Phase 5 のシグナル並べ替え用）。"""
    null = np.asarray(list(null_values), dtype="float64")
    n = null.size
    p_up = (1 + int((null >= observed).sum())) / (1 + n)
    p_lo = (1 + int((null <= observed).sum())) / (1 + n)
    return PermResult(observed, 0, n, p_up, p_lo, min(p_up, p_lo),
                      float((null < observed).mean() * 100),
                      float(null.mean()), float(null.std(ddof=1)), 1,
                      "シグナル値の並べ替えによる帰無分布")


def judge(res: Result, *, kind: str, perm: PermResult | None = None,
          n_tests: int = 1) -> tuple[bool, str]:
    """合否判定。差分4 の2本立て。

    kind="event": 事前登録された単一仮説のイベントスタディ
                  -> パーミュテーション検定（片側2.5%以内）+ PF>=1.3
    kind="scan" : 総当たり探索型 -> n>=100 + |t|>=2 + PF>=1.3 + Bonferroni
    """
    if kind == "scan":
        crit = bonferroni_t(n_tests)
        ok = (res.n >= N_MIN and abs(res.t) >= T_MIN and res.pf >= PF_MIN
              and abs(res.t) >= crit)
        if res.n < N_MIN:
            return False, f"取引回数 {res.n} 回で基準の{N_MIN}回未満。"
        if abs(res.t) < T_MIN:
            return False, f"コスト控除後 t={res.t:.2f} で |t|>={T_MIN} 未満。"
        if res.pf < PF_MIN:
            return False, f"コスト控除後 PF={res.pf:.2f} で基準の{PF_MIN}未満。"
        if abs(res.t) < crit:
            return False, (f"名目 t={res.t:.2f} は満たすが Bonferroni臨界 "
                           f"{crit:.2f}（検定{n_tests}回）に届かない。")
        return ok, f"コスト控除後 t={res.t:.2f} / PF={res.pf:.2f} / n={res.n}。"

    if perm is None or perm.observed != perm.observed:
        return False, "パーミュテーション検定を実行できなかった（候補日不足）。"
    if res.n == 0:
        return False, "取引が1件も成立しなかった。"
    if not perm.passed:
        return False, (f"パーミュテーション検定 片側p={perm.p_one_sided:.4f} で"
                       f"基準の {PERM_ALPHA} 以内に入らない"
                       f"（実測はランダム日程の{perm.percentile:.1f}パーセンタイル）。")
    if res.pf < PF_MIN:
        return False, (f"パーミュテーション検定は通るが、コスト控除後 "
                       f"PF={res.pf:.2f} で基準の{PF_MIN}未満。")
    return True, (f"パーミュテーション検定 片側p={perm.p_one_sided:.4f}"
                  f"（{perm.percentile:.1f}パーセンタイル）、"
                  f"コスト控除後 PF={res.pf:.2f} / n={res.n}。")


REGIME_MIN_N = 30


def regime_cell(res: Result) -> str:
    """レジーム別掲の表示ルール（差分4）。

    n >= 30 : 数値を出すが「参考値」ラベルを付け、単独では合否判定に使わない
    n <  30 : 「n不足のため算出せず」と明記し、無理に数字を出さない
    """
    if res.n < REGIME_MIN_N:
        return f"n={res.n} — n不足のため算出せず"
    return (f"n={res.n} 平均={res.mean_pips:.2f}pips t={res.t:.3f} "
            f"PF={res.pf:.3f}（参考値）")


def permutation_test_pooled(contribs: dict, event_dates, *,
                            n_iter: int = PERM_ITERS, seed: int = 20260915,
                            standardize: bool = True,
                            match_dow: bool = True, match_month: bool = True
                            ) -> PermResult:
    """複数通貨ペアをプールしたパーミュテーション検定（v1.1 差分4）。

    contribs : {通貨ペア: Series(index=候補営業日, value=寄与)}

    四半期末のドル調達仮説は USD 側の現象なので、EURUSD / GBPUSD / AUDUSD を
    プールして検定してよい（円クロスは円側要因が混入するので入れない）。

    重要: 1回の置換で **全ペアに同じランダム日付** を使う。ペアごとに別々の日を
    引くと、同じ日に一斉に動く（＝互いに相関した）という実際の構造が消えて
    帰無分布の分散が小さくなり、有意に見えすぎるため。

    standardize=True のとき各ペアの寄与を候補日全体で標準化してから足す
    （pips のスケールがペアごとに違うので、そのまま足すと値幅の大きいペアが
    支配してしまう）。
    """
    series = {}
    for k, v in contribs.items():
        s = pd.Series(v).dropna().astype("float64")
        s.index = pd.DatetimeIndex(s.index).normalize()
        series[k] = s[~s.index.duplicated()].sort_index()
    if not series:
        nan = float("nan")
        return PermResult(nan, 0, n_iter, nan, nan, nan, nan, nan, nan, 0, "系列なし")

    common = None
    for s in series.values():
        common = s.index if common is None else common.intersection(s.index)
    common = pd.DatetimeIndex(sorted(common))
    names = sorted(series)
    mat = np.column_stack([series[k].reindex(common).to_numpy() for k in names])
    if standardize:
        sd = mat.std(axis=0, ddof=1)
        sd[sd == 0] = 1.0
        mat = (mat - mat.mean(axis=0)) / sd

    ev = pd.DatetimeIndex(pd.DatetimeIndex(event_dates).normalize())
    ev = ev[ev.isin(common)]
    if len(ev) == 0:
        nan = float("nan")
        return PermResult(nan, 0, n_iter, nan, nan, nan, nan, nan, nan, 0,
                          "イベントが共通候補日に1件も一致しない")
    pos_of = {d: i for i, d in enumerate(common)}
    ev_pos = np.array([pos_of[d] for d in ev])
    observed = float(mat[ev_pos].mean())

    def key(idx: pd.DatetimeIndex):
        k = np.zeros(len(idx), dtype=np.int64)
        if match_dow:
            k = k * 7 + idx.dayofweek.to_numpy()
        if match_month:
            k = k * 13 + idx.month.to_numpy()
        return k

    cand_key, ev_key = key(common), key(ev)
    rng = np.random.default_rng(seed)
    totals = np.zeros(n_iter, dtype="float64")
    n_strata, shortfall = 0, []
    for k in np.unique(ev_key):
        need = int((ev_key == k).sum())
        pool = np.flatnonzero(cand_key == k)
        n_strata += 1
        if pool.size == 0:
            shortfall.append(f"層{k}: 候補日なし")
            continue
        if pool.size < need:
            shortfall.append(f"層{k}: 候補{pool.size}件 < 必要{need}件のため復元抽出")
            pick = rng.integers(0, pool.size, size=(n_iter, need))
        else:
            r = rng.random((n_iter, pool.size))
            pick = np.argpartition(r, need - 1, axis=1)[:, :need]
        # 同じランダム日付を全ペアに適用（ペア間の相関を保つ）
        totals += mat[pool[pick]].sum(axis=(1, 2))

    null = totals / (len(ev) * mat.shape[1])
    p_up = (1 + int((null >= observed).sum())) / (1 + n_iter)
    p_lo = (1 + int((null <= observed).sum())) / (1 + n_iter)
    return PermResult(observed, len(ev), n_iter, p_up, p_lo, min(p_up, p_lo),
                      float((null < observed).mean() * 100),
                      float(null.mean()), float(null.std(ddof=1)), n_strata,
                      "; ".join(shortfall) + f" / プール対象: {', '.join(names)}")
