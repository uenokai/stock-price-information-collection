"""ファンダメンタル・スクリーニングのテスト。"""

import numpy as np
import pandas as pd
import pytest

from stock_screener.screening.fundamental import (
    apply_market_cap_filter,
    score_fundamentals,
)

BASE_CFG = {
    "weights": {"roe": 0.5, "pe": 0.5},
    "min_metrics_required": 2,
    "min_sector_size": 5,
}


def make_df(**columns):
    n = len(next(iter(columns.values())))
    data = {"ticker": [f"T{i}" for i in range(n)], "sector": ["Tech"] * n}
    data.update(columns)
    return pd.DataFrame(data)


class TestScoreFundamentals:
    def test_high_roe_low_pe_wins(self):
        # ROEが高くPERが低い銘柄が最上位に来る
        df = make_df(
            roe=[0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
            pe=[30.0, 25.0, 20.0, 15.0, 10.0, 5.0],
        )
        result = score_fundamentals(df, BASE_CFG)
        assert result.iloc[0]["ticker"] == "T5"
        assert result.iloc[-1]["ticker"] == "T0"
        assert result["score"].is_monotonic_decreasing

    def test_negative_pe_treated_as_missing(self):
        # 赤字企業 (PER<=0) は「超割安」ではなく欠損として扱う
        df = make_df(
            roe=[0.10, 0.10, 0.10, 0.10, 0.10, 0.10],
            pe=[-5.0, 10.0, 15.0, 20.0, 25.0, 30.0],
        )
        result = score_fundamentals(df, BASE_CFG)
        t0 = result[result["ticker"] == "T0"]
        # PERが欠損扱いなので指標数は1 → min_metrics_required=2 を満たさず除外
        assert t0.empty

    def test_missing_metric_weight_renormalized(self):
        # 指標が1つ欠損しても、残りの指標で正規化されたスコアが付く
        cfg = dict(BASE_CFG, min_metrics_required=1)
        df = make_df(
            roe=[0.30, 0.10, 0.20, 0.15, 0.25, 0.05],
            pe=[np.nan, 10.0, 15.0, 20.0, 25.0, 30.0],
        )
        result = score_fundamentals(df, cfg)
        t0 = result[result["ticker"] == "T0"].iloc[0]
        # T0はROEが全体1位 (パーセンタイル1.0) なのでスコアはちょうど1.0
        assert t0["score"] == pytest.approx(1.0)
        assert t0["metric_coverage"] == 1

    def test_small_sector_falls_back_to_global(self):
        # 業種のサンプル数が少ない場合は全体パーセンタイルで評価される
        df = pd.DataFrame({
            "ticker": ["A", "B", "C", "D", "E", "F"],
            "sector": ["Tech", "Tech", "Tech", "Tech", "Tech", "Tiny"],
            "roe": [0.05, 0.10, 0.15, 0.20, 0.25, 0.30],
            "pe": [30.0, 25.0, 20.0, 15.0, 10.0, 5.0],
        })
        result = score_fundamentals(df, BASE_CFG)
        # 業種Tinyは1銘柄しかないが、全体では最良なので1位になる
        assert result.iloc[0]["ticker"] == "F"

    def test_unknown_weight_key_raises(self):
        df = make_df(roe=[0.1] * 6, pe=[10.0] * 6)
        cfg = dict(BASE_CFG, weights={"roe": 0.5, "typo_metric": 0.5})
        with pytest.raises(ValueError, match="typo_metric"):
            score_fundamentals(df, cfg)


class TestMarketCapFilter:
    def test_filters_small_and_missing(self):
        df = pd.DataFrame({
            "ticker": ["A", "B", "C"],
            "market_cap": [10_000.0, 500.0, np.nan],
            "min_market_cap": [1_000.0, 1_000.0, 1_000.0],
        })
        result = apply_market_cap_filter(df)
        assert result["ticker"].tolist() == ["A"]
