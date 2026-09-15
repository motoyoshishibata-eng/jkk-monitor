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
