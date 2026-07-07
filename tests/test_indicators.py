"""テクニカル指標のテスト。"""

import numpy as np
import pandas as pd
import pytest

from stock_screener.signals.indicators import rolling_high, rsi, sma


class TestSma:
    def test_basic(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = sma(s, 3)
        assert np.isnan(result.iloc[0])
        assert np.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[4] == pytest.approx(4.0)


class TestRsi:
    def test_all_gains_is_100(self):
        s = pd.Series(np.linspace(100, 200, 50))
        assert rsi(s, 14).iloc[-1] == pytest.approx(100.0)

    def test_all_losses_is_near_zero(self):
        s = pd.Series(np.linspace(200, 100, 50))
        assert rsi(s, 14).iloc[-1] == pytest.approx(0.0, abs=1e-6)

    def test_flat_is_50(self):
        s = pd.Series([100.0] * 50)
        assert rsi(s, 14).iloc[-1] == pytest.approx(50.0)

    def test_bounded_0_100(self):
        rng = np.random.default_rng(42)
        s = pd.Series(100 + rng.normal(0, 2, 300).cumsum())
        values = rsi(s, 14).dropna()
        assert ((values >= 0) & (values <= 100)).all()

    def test_warmup_is_nan(self):
        s = pd.Series(np.linspace(100, 120, 50))
        result = rsi(s, 14)
        assert result.iloc[:13].isna().all()
        assert result.iloc[14:].notna().all()


class TestRollingHigh:
    def test_excludes_current_day(self):
        # 最終日に新高値を付けた場合、rolling_high は前日までの高値を返す
        s = pd.Series([10.0, 12.0, 11.0, 15.0])
        result = rolling_high(s, 3)
        assert result.iloc[-1] == pytest.approx(12.0)
        assert s.iloc[-1] > result.iloc[-1]
