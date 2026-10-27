"""モデルポートフォリオ (ペーパー運用) のテスト (合成データ使用)。"""

import numpy as np
import pandas as pd

from stock_screener.model_portfolio import (
    benchmark_nav,
    load_state,
    rebalance,
    run_model_portfolio,
    save_state,
    select_targets,
    should_rebalance,
    value_of,
)


def make_prices(tickers, start="2026-09-01", n=40, base=100.0, step=1.0):
    idx = pd.bdate_range(start, periods=n)
    prices = {}
    for i, t in enumerate(tickers):
        close = base * (i + 1) + step * np.arange(n)  # 毎日 +step
        prices[t] = pd.DataFrame({"close": close}, index=idx)
    return prices


def targets_df(rows):
    return pd.DataFrame(rows, columns=["ticker", "name", "market", "score"])


class TestSelectTargets:
    def test_top_n_per_market(self):
        cand = pd.DataFrame({
            "ticker": ["A", "B", "C", "D", "E"],
            "name": list("ABCDE"),
            "market": ["jp", "jp", "jp", "us", "us"],
            "momentum_12_1": [0.1, 0.5, np.nan, 0.2, 0.3],
        })
        out = select_targets(cand, "momentum_12_1", 1)
        assert out["ticker"].tolist() == ["B", "E"]  # 市場ごとに1位。NaN は除外

    def test_missing_column_gives_empty(self):
        out = select_targets(pd.DataFrame({"ticker": ["A"], "market": ["jp"]}), "score", 3)
        assert out.empty


class TestRebalance:
    def test_initial_equal_weight_with_one_way_cost(self):
        prices = make_prices(["A", "B"])
        date = prices["A"].index[-1].strftime("%Y-%m-%d")
        targets = targets_df([["A", "A", "jp", 1], ["B", "B", "jp", 1]])
        h, trades, nav = rebalance(pd.DataFrame(columns=["portfolio", "ticker", "name", "market",
                                                          "units", "entry_date", "entry_price"]),
                                   1.0, targets, prices, date, "p", cost_rate=0.002)
        assert len(h) == 2 and (trades["side"] == "buy").sum() == 2
        assert abs(nav - (1.0 - 0.5 * 0.002)) < 1e-12  # 初回は片道分
        weights = h["units"] * h["entry_price"]
        assert np.allclose(weights, nav / 2)

    def test_swap_one_name_keeps_entry_of_continuing(self):
        prices = make_prices(["A", "B", "C"])
        d0 = prices["A"].index[5].strftime("%Y-%m-%d")
        d1 = prices["A"].index[-1].strftime("%Y-%m-%d")
        empty = pd.DataFrame(columns=["portfolio", "ticker", "name", "market", "units", "entry_date", "entry_price"])
        h0, _, nav0 = rebalance(empty, 1.0, targets_df([["A", "A", "jp", 1], ["B", "B", "jp", 1]]),
                                prices, d0, "p", 0.0)
        nav1 = value_of(h0, prices, d1)
        assert nav1 > nav0  # 値上がり
        h1, trades, nav_after = rebalance(h0, nav1, targets_df([["A", "A", "jp", 1], ["C", "C", "jp", 1]]),
                                          prices, d1, "p", 0.002)
        assert set(h1["ticker"]) == {"A", "C"}
        assert trades["side"].tolist() == ["sell", "buy"]
        assert trades.iloc[0]["ticker"] == "B" and trades.iloc[1]["ticker"] == "C"
        a = h1.set_index("ticker").loc["A"]
        assert a["entry_date"] == d0  # 継続銘柄は取得日を引き継ぐ
        assert abs(nav_after - nav1 * (1 - 0.5 * 0.002)) < 1e-12  # 1/2 入替

    def test_unpriced_target_is_skipped(self):
        prices = make_prices(["A"])
        date = prices["A"].index[-1].strftime("%Y-%m-%d")
        empty = pd.DataFrame(columns=["portfolio", "ticker", "name", "market", "units", "entry_date", "entry_price"])
        h, _, _ = rebalance(empty, 1.0, targets_df([["A", "A", "jp", 1], ["ZZZ", "Z", "jp", 1]]),
                            prices, date, "p", 0.0)
        assert h["ticker"].tolist() == ["A"]


class TestHelpers:
    def test_should_rebalance_on_month_change(self):
        assert should_rebalance(None, "2026-11-02")
        assert should_rebalance("2026-10-20", "2026-11-02")
        assert not should_rebalance("2026-11-02", "2026-11-20")

    def test_benchmark_nav_equal_weight(self):
        prices = make_prices(["X", "Y"], base=100.0, step=0.0)
        prices["Y"]["close"] = prices["Y"]["close"] * np.linspace(1.0, 1.5, len(prices["Y"]))
        d0 = prices["X"].index[0].strftime("%Y-%m-%d")
        d1 = prices["X"].index[-1].strftime("%Y-%m-%d")
        assert abs(benchmark_nav(prices, ["X", "Y"], d0, d1) - 1.25) < 1e-9


class TestRunAndState:
    def test_inception_then_daily_then_monthly_rebalance(self, tmp_path):
        prices = make_prices(["A", "B", "C", "BENCH"], start="2026-09-20", n=40)
        dates = [d.strftime("%Y-%m-%d") for d in prices["A"].index]
        state = load_state(tmp_path)
        t_momentum = targets_df([["A", "A", "jp", 1], ["B", "B", "jp", 1]])
        t_composite = targets_df([["C", "C", "jp", 1]])

        # 1日目: 新規構築
        d_sep = next(d for d in dates if d.startswith("2026-09"))
        state, summary = run_model_portfolio(state, {"momentum": t_momentum, "composite": t_composite},
                                             prices, ["BENCH"], d_sep, 0.002)
        assert summary["momentum"]["rebalanced_today"]
        assert len(summary["momentum"]["holdings"]) == 2 and len(summary["composite"]["holdings"]) == 1
        assert abs(summary["momentum"]["bench_nav"] - 1.0) < 1e-12
        save_state(tmp_path, state)

        # 同月の翌営業日: 評価のみ (入れ替えなし)
        d_sep2 = dates[dates.index(d_sep) + 1]
        state = load_state(tmp_path)
        state, summary = run_model_portfolio(state, {"momentum": t_momentum, "composite": t_composite},
                                             prices, ["BENCH"], d_sep2, 0.002)
        assert not summary["momentum"]["rebalanced_today"]
        assert summary["momentum"]["nav"] > 1.0 - 0.001  # 値上がりで評価額が上がる
        assert summary["momentum"]["inception"] == d_sep

        # 翌月初: 入れ替え (B → C)
        d_oct = next(d for d in dates if d.startswith("2026-10"))
        state, summary = run_model_portfolio(state, {"momentum": targets_df([["A", "A", "jp", 1], ["C", "C", "jp", 1]]),
                                                     "composite": t_composite},
                                             prices, ["BENCH"], d_oct, 0.002)
        assert summary["momentum"]["rebalanced_today"]
        assert set(summary["momentum"]["holdings"]["ticker"]) == {"A", "C"}
        assert summary["momentum"]["last_rebalance"] == d_oct
        save_state(tmp_path, state)
        reloaded = load_state(tmp_path)
        assert len(reloaded["nav"]) == 6  # 2戦略 × 3日
        assert (reloaded["trades"]["side"] == "sell").sum() == 1

    def test_same_day_rerun_is_idempotent(self, tmp_path):
        prices = make_prices(["A", "BENCH"], start="2026-10-01", n=5)
        d = prices["A"].index[-1].strftime("%Y-%m-%d")
        t = {"momentum": targets_df([["A", "A", "jp", 1]]), "composite": targets_df([["A", "A", "jp", 1]])}
        state = load_state(tmp_path)
        state, _ = run_model_portfolio(state, t, prices, ["BENCH"], d, 0.002)
        state, summary = run_model_portfolio(state, t, prices, ["BENCH"], d, 0.002)
        assert len(state["nav"]) == 2 and len(state["holdings"]) == 2
        # 売買ゼロの再実行でも初回の買い記録は残り、レポート用の直近入れ替えにも出る
        assert (state["trades"]["side"] == "buy").sum() == 2
        assert len(summary["momentum"]["trades_last_rebalance"]) == 1
