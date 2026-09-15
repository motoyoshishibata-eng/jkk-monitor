"""検証ロジックそのものが正しいかのテスト。

相場の結果ではなく「計算が合っているか」だけを見る。
ここが壊れていると、以降の全 Phase の数字が静かに間違う。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from usdjpy_research.common import tz, events
from usdjpy_research.common.backtest import simulate, simulate_stop_entry
from usdjpy_research.common.cost import CostModel, count_rollovers
from usdjpy_research.common import stats as S
from usdjpy_research.common.stats import (ndtri, bonferroni_t, summarize,
                                          newey_west_t, zscore)


# --- タイムゾーン ---------------------------------------------------------
@pytest.mark.parametrize("year,start,end", [
    (2007, dt.date(2007, 3, 11), dt.date(2007, 11, 4)),
    (2024, dt.date(2024, 3, 10), dt.date(2024, 11, 3)),
    (2026, dt.date(2026, 3, 8), dt.date(2026, 11, 1)),
])
def test_us_dst_boundaries(year, start, end):
    assert tz.us_dst_start(year) == start
    assert tz.us_dst_end(year) == end


def test_fomc_server_hour_matches_spec_table():
    """指示書 §0-2 の表: FOMC はサーバー21:00 固定 -> 夏 JST03:00 / 冬 JST04:00。"""
    assert tz.server_to_jst(dt.datetime(2024, 7, 31, 21, 0)).hour == 3
    assert tz.server_to_jst(dt.datetime(2024, 12, 18, 21, 0)).hour == 4


def test_tokyo_fix_shifts_but_us_events_do_not():
    assert tz.tokyo_fix_server_hm(dt.date(2024, 7, 1)) == (3, 55)
    assert tz.tokyo_fix_server_hm(dt.date(2024, 1, 15)) == (2, 55)
    assert tz.FOMC_SERVER_HOUR == 21 and tz.NY_OPTION_CUT_SERVER_HOUR == 17


def test_london_fix_autumn_misalignment():
    """英米DSTのズレ期間だけ 19:00 になる（指示書 §0-2 の『秋ズレあり』）。"""
    assert tz.london_fix_server_hour(dt.date(2024, 1, 15)) == 18   # 両方冬
    assert tz.london_fix_server_hour(dt.date(2024, 4, 15)) == 18   # 両方夏
    assert tz.london_fix_server_hour(dt.date(2024, 3, 15)) == 19   # 米夏・英冬
    assert tz.london_fix_server_hour(dt.date(2024, 10, 29)) == 19  # 米夏・英冬


# --- 統計 -----------------------------------------------------------------
@pytest.mark.parametrize("p,want", [(0.975, 1.959964), (0.995, 2.575829), (0.5, 0.0)])
def test_ndtri_known_values(p, want):
    assert abs(ndtri(p) - want) < 1e-5


def test_bonferroni_matches_preregistered_counts():
    assert abs(bonferroni_t(1) - 1.959964) < 1e-5
    assert abs(bonferroni_t(12) - 2.8652) < 1e-3   # Phase 4 の 12検定


def test_summarize_pf_and_t():
    r = summarize([2.0, 2.0, -1.0, -1.0])
    assert r.n == 4 and r.pf == pytest.approx(2.0) and r.win_rate == 0.5
    assert r.total_pips == pytest.approx(2.0)


def test_newey_west_deflates_overlapping_windows():
    rng = np.random.default_rng(0)
    w = rng.normal(0, 1, 4000)
    overlap = np.convolve(w, np.ones(5) / 5, mode="valid")
    assert abs(newey_west_t(overlap)) < abs(summarize(overlap).t)


def test_zscore_has_no_lookahead():
    s = pd.Series(range(300), dtype="float64")
    z = zscore(s, 250)
    # 当日を含まない基準（prev = shift(1)）なので、最初に定義できるのは index 250
    assert z.iloc[:250].isna().all() and z.iloc[250:].notna().all()
    # 当日の値を差し替えても過去の Z は動かない = 先読みしていない
    s2 = s.copy(); s2.iloc[260] = 9999.0
    assert zscore(s2, 250).iloc[:260].equals(z.iloc[:260])


# --- コスト ---------------------------------------------------------------
@pytest.mark.parametrize("a,b,want", [
    ((2024, 4, 1), (2024, 4, 2), 1),    # 月->火
    ((2024, 4, 3), (2024, 4, 4), 3),    # 水跨ぎは3倍
    ((2024, 4, 5), (2024, 4, 8), 1),    # 金->月（土日は付かない）
    ((2024, 4, 1), (2024, 4, 8), 7),
])
def test_rollover_count(a, b, want):
    assert count_rollovers(dt.datetime(*a, 17), dt.datetime(*b, 17)) == want


def test_cost_uses_entry_hour_spread():
    m = CostModel(name="x", spread_by_hour={0: 5.0, 17: 1.0}, default_spread_pips=9.9)
    assert m.entry_exit_cost(dt.datetime(2024, 4, 1, 17)) == pytest.approx(1.0)
    assert m.entry_exit_cost(dt.datetime(2024, 4, 1, 0)) == pytest.approx(5.0)
    assert m.entry_exit_cost(dt.datetime(2024, 4, 1, 9)) == pytest.approx(9.9)


def test_load_broker_refuses_to_guess_when_unmeasured():
    """スプレッド未実測のとき黙って固定値に落ちない（指示書 §0-3 の禁止事項）。"""
    from usdjpy_research.common.cost import load_broker
    with pytest.raises(FileNotFoundError):
        load_broker("no_such_broker_xyz")


# --- バックテスト ---------------------------------------------------------
@pytest.fixture
def bars():
    idx = pd.date_range("2024-04-01 00:00", "2024-04-01 05:00", freq="h")
    return pd.DataFrame({
        "open":  [150.00, 150.10, 150.20, 149.50, 149.80, 150.00],
        "high":  [150.05, 150.15, 150.25, 150.25, 149.90, 150.10],
        "low":   [149.98, 150.05, 149.40, 149.45, 149.70, 149.90],
        "close": [150.02, 150.12, 150.15, 149.60, 149.85, 150.05],
    }, index=idx)


def test_simulate_requires_stop_loss(bars):
    with pytest.raises(ValueError):
        simulate(bars, [], CostModel.flat("x", 1.0), sl_pips=None)


def test_simulate_holds_to_exit_when_sl_far(bars):
    tr = simulate(bars, [(dt.datetime(2024, 4, 1, 1), dt.datetime(2024, 4, 1, 4), 1, "t")],
                  CostModel.flat("x", 1.0), sl_pips=500)
    assert len(tr) == 1
    assert tr.gross_pips.iloc[0] == pytest.approx(-30.0)   # 150.10 -> 149.80
    assert tr.net_pips.iloc[0] == pytest.approx(-31.0)     # スプレッド1pip


def test_simulate_sl_fills_worse_than_the_level(bars):
    tr = simulate(bars, [(dt.datetime(2024, 4, 1, 1), dt.datetime(2024, 4, 1, 4), 1, "t")],
                  CostModel.flat("x", 1.0), sl_pips=50, sl_slippage_pips=3)
    assert bool(tr.hit_sl.iloc[0])
    assert tr.gross_pips.iloc[0] == pytest.approx(-53.0)   # -50 - 3pips のスリップ


def test_stop_entry_skips_untouched_trigger(bars):
    c = CostModel.flat("x", 1.0)
    hit = simulate_stop_entry(bars, [("2024-04-01 00:00", "2024-04-01 02:00", 1,
                                      150.20, "2024-04-01 04:00", "t")], c, sl_pips=100)
    miss = simulate_stop_entry(bars, [("2024-04-01 00:00", "2024-04-01 02:00", 1,
                                       199.00, "2024-04-01 04:00", "t")], c, sl_pips=100)
    assert len(hit) == 1 and len(miss) == 0


def test_stop_entry_slips_against_us(bars):
    tr = simulate_stop_entry(bars, [("2024-04-01 00:00", "2024-04-01 02:00", 1,
                                     150.20, "2024-04-01 04:00", "t")],
                             CostModel.flat("x", 1.0), sl_pips=100,
                             entry_slippage_pips=2)
    assert tr.entry_price.iloc[0] == pytest.approx(150.22)


def test_simulate_skips_trade_across_a_data_gap():
    """週末の穴を無視して遠い価格で約定させない。"""
    idx = pd.DatetimeIndex(["2024-04-05 17:00", "2024-04-08 17:00"])
    df = pd.DataFrame({"open": [150.0, 155.0], "high": [150.1, 155.1],
                       "low": [149.9, 154.9], "close": [150.0, 155.0]}, index=idx)
    tr = simulate(df, [(dt.datetime(2024, 4, 6, 17), dt.datetime(2024, 4, 8, 17), 1, "t")],
                  CostModel.flat("x", 1.0), sl_pips=100)
    assert len(tr) == 0


# --- FOMC サイクル日 ------------------------------------------------------
def test_fomc_cycle_day_labels_match_preregistration():
    td = pd.bdate_range("2024-01-01", "2024-04-30")
    cd = events.fomc_cycle_day(td, pd.DatetimeIndex(["2024-01-31", "2024-03-20"]))
    assert cd[pd.Timestamp("2024-01-31")] == 0      # 声明発表日
    assert cd[pd.Timestamp("2024-01-30")] == -1     # 前営業日
    assert cd[pd.Timestamp("2024-02-01")] == 1
    assert cd[pd.Timestamp("2024-03-19")] == -1     # 次回FOMCの前営業日で上書き


def test_fomc_calendar_structure():
    """年8回・間隔40〜56日・重複なし。日付そのものの正しさは別途要突合。"""
    sch = events.load_fomc(scheduled_only=True)
    per_year = sch.groupby(sch["date"].dt.year).size()
    assert (per_year == 8).all(), dict(per_year[per_year != 8])
    gaps = sch["date"].diff().dt.days.dropna()
    assert gaps.min() >= 30 and gaps.max() <= 70
    assert not sch["date"].duplicated().any()


def test_fomc_calendar_is_flagged_unverified():
    """出典と突き合わせるまでは audit が必ず落ちること（黙って通さない）。"""
    ok, msgs = events.audit_fomc()
    raw = events.load_fomc(scheduled_only=False)
    unverified = (raw["verified"].astype(str).str.lower() != "yes").any()
    assert (not ok) == bool(unverified)
    assert any("federalreserve.gov" in m for m in msgs) or not unverified


# --- 事前登録した定数が動いていないこと -----------------------------------
def test_preregistered_constants_unchanged():
    from usdjpy_research.phases import (phase1_fomc_cycle as p1, phase3_ny_cut as p3,
                                        phase4_click365 as p4, phase5_rebalance as p5)
    assert sorted(p1.EVEN_WEEK_DAYS) == (list(range(-1, 4)) + list(range(9, 14))
                                         + list(range(19, 24)) + list(range(29, 34)))
    assert (p3.CUT_HOUR, p3.PRE_HOUR) == (17, 16)
    assert (p3.LOOKBACK_DAYS, p3.NARROW_PCTL, p3.BREAK_BUFFER_PIPS) == (20, 0.25, 3.0)
    assert p4.Z_THRESHOLDS == (1.5, 2.0) and p4.HOLD_DAYS == (1, 5, 20)
    assert p4.N_TESTS == 12
    assert (p5.HEDGE_EQUITY, p5.HEDGE_BOND, p5.WINDOW_DAYS) == (0.50, 0.80, 3)


# =====================================================================
# 指示書 v1.1 の差分に対するテスト
# =====================================================================

# --- 差分1: ロンドンFIX の英米DSTギャップ ---
@pytest.mark.parametrize("d,want", [
    ("2024-01-15", 18),   # 両方冬
    ("2024-04-15", 18),   # 両方夏
    ("2024-07-01", 18),   # 両方夏
    ("2024-03-15", 19),   # 米のみ夏（3月第2日曜〜3月最終日曜）
    ("2024-10-29", 19),   # EUのみ冬（10月最終日曜〜11月第1日曜）
    ("2024-11-05", 18),   # 両方冬に戻る
])
def test_london_fix_gap_periods(d, want):
    assert tz.london_fix_server_hour(dt.date.fromisoformat(d)) == want


def test_gap_periods_exist_every_year():
    """ギャップ期間は毎年2回、必ず発生する（検証(D)の前提）。"""
    for y in (2007, 2015, 2024, 2026):
        days = pd.date_range(f"{y}-01-01", f"{y}-12-31", freq="D")
        gap = [d for d in days if tz.is_us_dst(d.date()) and not tz.is_uk_dst(d.date())]
        spring = [d for d in gap if d.month == 3]
        autumn = [d for d in gap if d.month in (10, 11)]
        assert spring and autumn, (y, len(spring), len(autumn))


# --- 差分2: FOMC 発表時刻の時期テーブル ---
@pytest.mark.parametrize("date,pc,want_et,want_server", [
    ("2007-01-31", "no", "14:15", dt.time(21, 15)),       # 記者会見制度が無い時期
    ("2011-03-15", "no", "14:15", dt.time(21, 15)),
    ("2011-04-27", "no", "14:15", dt.time(21, 15)),       # 会見なし会合は 14:15 と分かる
    ("2011-04-27", "yes", None, None),                    # 会見あり -> 要確認
    ("2012-06-20", "unknown", None, None),                # 不明 -> 要確認
    ("2013-03-12", "unknown", None, None),                # 統一の前日まで不明
    ("2013-03-13", "unknown", "14:00", dt.time(21, 0)),   # 統一以降は会見有無によらず 14:00
    ("2024-01-31", "yes", "14:00", dt.time(21, 0)),
])
def test_fomc_announcement_time_era_table(date, pc, want_et, want_server):
    d = dt.date.fromisoformat(date)
    assert events.announcement_time_et(d, pc) == want_et
    assert events.announcement_server_time(d, pc) == want_server


def test_et_to_server_offset_is_year_round_seven_hours():
    """ET -> サーバー時刻は夏冬とも +7時間（片方だけDSTがずれたりしない）。"""
    assert events.ET_TO_SERVER_HOURS == 7
    for d in ("2024-07-31", "2024-12-18"):
        dd = dt.date.fromisoformat(d)
        assert events.announcement_server_time(dd, "yes") == dt.time(21, 0)
        assert tz.server_to_et(dt.datetime.combine(dd, dt.time(21, 0))).hour == 14


def test_unknown_times_are_not_guessed():
    """要確認の会合が黙って埋められていないこと（差分2の禁止事項）。"""
    f = events.load_fomc(scheduled_only=False)
    unknown = f[~f["time_known"]]
    assert len(unknown) > 0, "2011-2013 の会見あり会合が unknown になっていない"
    assert unknown["date"].dt.year.between(2011, 2013).all()
    assert (unknown["press_conference"].astype(str).str.lower() == "unknown").all()


def test_provided_2021_2027_rows_are_verified():
    f = events.load_fomc(scheduled_only=False)
    recent = f[f["date"] >= "2021-01-01"]
    assert len(recent) == 56
    assert set(recent["verified"].astype(str)) <= {"yes", "scheduled", "tentative"}
    assert (recent["announcement_time_et"] == "14:00").all()


# --- 差分4: パーミュテーション検定 ---
def _fake_contrib(seed=0, n_days=5000):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2007-01-01", periods=n_days)
    return pd.Series(rng.normal(0, 25, n_days), index=days), days, rng


def test_permutation_null_is_not_significant():
    contrib, days, rng = _fake_contrib(1)
    ev = days[rng.choice(len(days), 150, replace=False)]
    r = S.permutation_test(contrib, ev, n_iter=1000)
    assert not r.passed and r.p_one_sided > 0.05


def test_permutation_detects_a_real_effect():
    contrib, days, rng = _fake_contrib(2)
    ev = days[rng.choice(len(days), 150, replace=False)]
    contrib.loc[ev] += 30
    r = S.permutation_test(contrib, ev, n_iter=1000)
    assert r.passed and r.p_one_sided <= S.PERM_ALPHA


def test_permutation_matches_day_of_week_and_month():
    """火水に偏ったイベントなら、帰無分布も火水からしか引かないこと。"""
    contrib, days, _ = _fake_contrib(3)
    tw = days[days.dayofweek.isin([1, 2])]
    r = S.permutation_test(contrib, tw[:120], n_iter=200)
    assert r.n_strata <= 2 * 12          # 曜日2 × 月12 が上限
    assert r.strata_note.strip("; ") == ""   # 復元抽出に落ちていない


def test_permutation_p_value_never_zero():
    """add-one 補正があるので p=0 にはならない（10,000回でも 1/10001 が下限）。"""
    contrib, days, rng = _fake_contrib(4)
    ev = days[rng.choice(len(days), 100, replace=False)]
    contrib.loc[ev] += 500
    r = S.permutation_test(contrib, ev, n_iter=500)
    assert r.p_one_sided > 0


def test_permutation_multi_separates_window_shapes():
    contrib, days, rng = _fake_contrib(5)
    other = pd.Series(rng.normal(0, 5, len(days)), index=days)
    ev = days[rng.choice(len(days), 120, replace=False)]
    evm = [(d, "A") for d in ev[:60]] + [(d, "B") for d in ev[60:]]
    r = S.permutation_test_multi({"A": contrib, "B": other}, evm, n_iter=500)
    assert 0 < r.p_one_sided <= 1 and r.n_events == 120


def test_pooled_permutation_uses_same_dates_across_pairs():
    """ペア間の相関を保つと帰無分布の分散が縮まない（=有意に見えすぎない）。"""
    rng = np.random.default_rng(6)
    days = pd.bdate_range("2007-01-01", periods=3000)
    shock = rng.normal(0, 1, len(days))
    pairs = {p: pd.Series(shock * 5 + rng.normal(0, 15, len(days)), index=days)
             for p in ("EURUSD", "GBPUSD", "AUDUSD")}
    ev = days[rng.choice(len(days), 70, replace=False)]
    r = S.permutation_test_pooled(pairs, ev, n_iter=1000)
    assert not r.passed
    for p in pairs:
        pairs[p] = pairs[p].copy()
        pairs[p].loc[ev] += 25
    r2 = S.permutation_test_pooled(pairs, ev, n_iter=1000)
    assert r2.passed and "EURUSD" in r2.strata_note


# --- 差分4: 判定ロジックとレジーム表示 ---
def test_judge_event_ignores_n100_but_keeps_pf():
    good = S.Result(n=40, mean_pips=5, sd_pips=20, t=1.6, p=0.1, pf=1.5,
                    win_rate=0.6, total_pips=200, max_dd_pips=50)
    perm_pass = S.PermResult(5, 40, 10000, 0.01, 0.99, 0.01, 99.0, 0, 1, 5)
    ok, why = S.judge(good, kind="event", perm=perm_pass)
    assert ok, why                     # n=40 でも通る（差分4）
    weak_pf = S.Result(n=40, mean_pips=5, sd_pips=20, t=1.6, p=0.1, pf=1.1,
                       win_rate=0.6, total_pips=200, max_dd_pips=50)
    ok2, why2 = S.judge(weak_pf, kind="event", perm=perm_pass)
    assert not ok2 and "PF" in why2    # PF>=1.3 は維持される


def test_judge_scan_still_requires_n100_and_bonferroni():
    r = S.Result(n=80, mean_pips=5, sd_pips=20, t=3.0, p=0.01, pf=1.5,
                 win_rate=0.6, total_pips=400, max_dd_pips=50)
    ok, why = S.judge(r, kind="scan", n_tests=12)
    assert not ok and "100回未満" in why
    r2 = S.Result(n=200, mean_pips=5, sd_pips=20, t=2.3, p=0.02, pf=1.5,
                  win_rate=0.6, total_pips=1000, max_dd_pips=50)
    ok2, why2 = S.judge(r2, kind="scan", n_tests=12)
    assert not ok2 and "Bonferroni" in why2


def test_regime_cell_thresholds():
    rng = np.random.default_rng(7)
    assert "n不足のため算出せず" in S.regime_cell(S.summarize(rng.normal(1, 10, 29)))
    assert "参考値" in S.regime_cell(S.summarize(rng.normal(1, 10, 30)))


# --- 差分5: ファイル名 ---
def test_results_go_to_preregistration_not_project_md():
    from usdjpy_research.common import report
    assert report.PREREG_MD.name == "PREREGISTRATION.md"
    assert not (report.ROOT / "PROJECT.md").exists()


# --- 上位足への集約が SL 判定を変えないこと ---
def test_resample_preserves_high_low_touch():
    from usdjpy_research.common.io import resample_bars
    idx = pd.date_range("2024-04-01 00:00", periods=180, freq="min")
    rng = np.random.default_rng(8)
    close = 150 + np.cumsum(rng.normal(0, 0.01, 180))
    df = pd.DataFrame({"open": close, "high": close + 0.01,
                       "low": close - 0.01, "close": close}, index=idx)
    h1 = resample_bars(df, "1h")
    assert len(h1) == 3
    for ts, row in h1.iterrows():
        m = df.loc[ts:ts + pd.Timedelta(minutes=59)]
        assert row["high"] == m["high"].max() and row["low"] == m["low"].min()
        assert row["open"] == m["open"].iloc[0] and row["close"] == m["close"].iloc[-1]


# --- 事前登録した定数（v1.1 追加分） ---
def test_v11_constants():
    from usdjpy_research.phases import (phase2_pre_fomc as p2,
                                        phase6_quarter_end as p6)
    assert p2.EXIT_OFFSET_MIN == 5
    assert p2.SLIPPAGE_SCENARIOS == (0.0, 3.0, 5.0)
    assert p6.POOL_SYMBOLS == ("EURUSD", "GBPUSD", "AUDUSD")
    assert "JPY" not in "".join(p6.POOL_SYMBOLS)   # 円クロスは入れない
    assert S.PERM_ITERS == 10_000 and S.PERM_ALPHA == 0.025
