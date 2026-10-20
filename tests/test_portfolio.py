"""ポートフォリオ・バックテストのテスト (合成データ使用)。"""

import numpy as np
import pandas as pd

from stock_screener.portfolio import (
    build_close_panel,
    buy_and_hold,
    latest_momentum,
    momentum_score,
    performance_metrics,
    rebalance_dates,
    run_portfolio_backtest,
    simulate,
    trend_mask,
    yearly_returns,
)


def make_prices(n_days=800, n_tickers=6, seed=0, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=n_days)
    rng = np.random.default_rng(seed)
    prices = {}
    for i in range(n_tickers):
        # 銘柄番号が大きいほどドリフトが大きい → モメンタム上位 = 高番号
        drift = 0.0003 * i
        close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n_days)))
        prices[f"T{i}"] = pd.DataFrame({"close": close}, index=idx)
    return prices


class TestPanelAndScores:
    def test_panel_shape_and_momentum_definition(self):
        prices = make_prices()
        panel = build_close_panel(prices)
        assert panel.shape == (800, 6)
        mom = momentum_score(panel, lookback=252, skip=21)
        t = panel.index[300]
        expected = panel["T0"].iloc[300 - 21] / panel["T0"].iloc[300 - 252] - 1
        assert abs(mom.loc[t, "T0"] - expected) < 1e-12
        assert mom.iloc[:252].isna().all().all()

    def test_latest_momentum_matches_last_row(self):
        prices = make_prices()
        mom = latest_momentum(prices)
        panel = build_close_panel(prices)
        assert abs(mom["T3"] - momentum_score(panel).iloc[-1]["T3"]) < 1e-12

    def test_latest_momentum_ignores_other_market_holidays(self):
        # 別市場の祝日 (自分は休場) があっても自分の営業日で数えるので NaN にならない
        prices = make_prices(n_tickers=1)
        jp = prices["T0"].copy()
        jp = jp.drop(jp.index[-30])  # 自市場の祝日を1日落とす
        prices["JP"] = jp
        mom = latest_momentum(prices)
        assert not np.isnan(mom["JP"])

    def test_trend_mask(self):
        prices = {"UP": pd.DataFrame({"close": np.linspace(100, 200, 300)},
                                      index=pd.bdate_range("2020-01-01", periods=300))}
        mask = trend_mask(build_close_panel(prices), 200)
        assert bool(mask["UP"].iloc[-1])
        assert mask["UP"].iloc[:199].isna().all() or not mask["UP"].iloc[:199].any()

    def test_rebalance_dates_first_business_day_of_month(self):
        idx = pd.bdate_range("2020-01-01", "2020-03-31")
        dates = rebalance_dates(idx, "M")
        assert [d.strftime("%Y-%m-%d") for d in dates] == ["2020-01-01", "2020-02-03", "2020-03-02"]


class TestSimulate:
    def test_single_stock_equals_buy_and_hold_without_cost(self):
        prices = make_prices(n_tickers=1)
        panel = build_close_panel(prices)
        start = panel.index[10]
        res = simulate(panel, None, None, None, "M", cost_rate=0.0, start=start)
        bh = buy_and_hold(panel["T0"], start)
        # 月次リバランスしても1銘柄なら買い持ちと一致する (開始日を揃えて比較)
        common = res["value"].index.intersection(bh.index)
        a = res["value"].loc[common] / res["value"].loc[common].iloc[0]
        b = bh.loc[common] / bh.loc[common].iloc[0]
        assert np.allclose(a, b)
        assert res["turnover"] == 0.0 or res["turnover"] < 1e-9

    def test_top_n_selects_highest_score(self):
        prices = make_prices()
        panel = build_close_panel(prices)
        mom = momentum_score(panel)
        res = simulate(panel, mom, 2, None, "M", 0.0, panel.index[260])
        first_date = next(iter(res["holdings"]))
        chosen = res["holdings"][first_date]
        expected = mom.loc[first_date].dropna().sort_values(ascending=False).index[:2].tolist()
        assert chosen == expected

    def test_cost_reduces_value(self):
        prices = make_prices()
        panel = build_close_panel(prices)
        mom = momentum_score(panel)
        free = simulate(panel, mom, 2, None, "M", 0.0, panel.index[260])["value"]
        paid = simulate(panel, mom, 2, None, "M", 0.01, panel.index[260])["value"]
        assert paid.iloc[-1] < free.iloc[-1]

    def test_no_eligible_means_cash(self):
        prices = make_prices(n_tickers=2)
        panel = build_close_panel(prices)
        eligible = pd.DataFrame(False, index=panel.index, columns=panel.columns)
        res = simulate(panel, None, None, eligible, "M", 0.002, panel.index[5])
        assert np.allclose(res["value"], 1.0)

    def test_metrics_and_yearly(self):
        idx = pd.bdate_range("2020-01-01", periods=504)
        value = pd.Series(np.linspace(1.0, 1.44, 504), index=idx)
        m = performance_metrics(value)
        assert abs(m["total"] - 0.44) < 1e-9
        assert 0.15 < m["cagr"] < 0.25
        assert m["max_dd"] == 0.0
        yr = yearly_returns(value)
        assert set(yr.index) == {2020, 2021}
        assert yr.loc[2020] > 0 and yr.loc[2021] > 0


class TestRunPortfolioBacktest:
    def test_end_to_end_per_market(self):
        prices = make_prices(n_days=700, n_tickers=4)
        prices["BENCH"] = pd.DataFrame(
            {"close": np.linspace(100, 150, 700)}, index=pd.bdate_range("2019-01-01", periods=700))
        meta = pd.DataFrame({"ticker": ["T0", "T1", "T2", "T3"], "market": ["jp"] * 4})
        cfg = {"top_n_per_market": 2, "rebalance": "M", "cost_rate": 0.002,
               "momentum_lookback": 252, "momentum_skip": 21, "trend_window": 200}
        results = run_portfolio_backtest(prices, meta, {"jp": "BENCH"}, cfg)
        assert "jp" in results
        strategies = results["jp"]["strategies"]
        assert set(strategies) == {"index", "universe_ew", "momentum_top", "momentum_trend", "trend_only"}
        for name, res in strategies.items():
            assert not np.isnan(res["metrics"]["cagr"]), name
        assert results["jp"]["n_tickers"] == 4
        assert not results["jp"]["yearly"].empty

    def test_short_history_is_skipped(self):
        prices = make_prices(n_days=100, n_tickers=2)
        prices["BENCH"] = prices["T0"].copy()
        meta = pd.DataFrame({"ticker": ["T0", "T1"], "market": ["jp"] * 2})
        results = run_portfolio_backtest(prices, meta, {"jp": "BENCH"}, {})
        assert results == {}


class TestGlitchRemoval:
    def test_temporary_crash_is_filled(self):
        from stock_screener.data.prices import _remove_glitches
        idx = pd.bdate_range("2024-01-01", periods=10)
        close = np.array([100, 101, 102, 10.0, 10.5, 103, 104, 105, 106, 107])
        df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                           "volume": 1.0}, index=idx)
        out = _remove_glitches(df, "X")
        assert out["close"].iloc[3] == 102 and out["close"].iloc[4] == 102
        assert out["close"].iloc[5] == 103

    def test_real_crash_is_kept(self):
        from stock_screener.data.prices import _remove_glitches
        idx = pd.bdate_range("2024-01-01", periods=10)
        close = np.array([100, 101, 102, 40.0, 41, 42, 43, 44, 45, 46])  # 戻らない下落は本物
        df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close,
                           "volume": 1.0}, index=idx)
        out = _remove_glitches(df, "X")
        assert out["close"].iloc[3] == 40.0
