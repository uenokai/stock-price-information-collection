"""テクニカル・シグナル判定のテスト (合成データ使用)。"""

import numpy as np
import pandas as pd

from stock_screener.signals.technical import detect_signals, scan_watchlist

PARAMS = {
    "golden_cross_lookback": 5,
    "breakout_volume_mult": 1.5,
    "pullback_rsi_min": 30,
    "pullback_rsi_max": 45,
    "volume_surge_mult": 3.0,
    "volume_surge_min_return": 0.02,
}


def make_ohlcv(close, volume=None):
    close = np.asarray(close, dtype=float)
    if volume is None:
        volume = np.full(len(close), 1_000_000.0)
    index = pd.bdate_range("2023-01-02", periods=len(close))
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": np.asarray(volume, dtype=float),
    }, index=index)


def v_shape(n_down=250, n_up=100, start=150.0, bottom=100.0, top=160.0):
    """下落→上昇のV字。上昇途中で50日SMAが200日SMAを上抜ける。"""
    down = np.linspace(start, bottom, n_down)
    up = np.linspace(bottom, top, n_up + 1)[1:]
    return np.concatenate([down, up])


class TestDetectSignals:
    def test_insufficient_history_returns_empty(self):
        df = make_ohlcv(np.full(100, 100.0))
        signals, metrics = detect_signals(df, PARAMS)
        assert signals == []
        assert metrics == {}

    def test_flat_market_no_signals(self):
        df = make_ohlcv(np.full(300, 100.0))
        signals, _ = detect_signals(df, PARAMS)
        assert signals == []

    def test_golden_cross_detected_with_wide_lookback(self):
        df = make_ohlcv(v_shape())
        params = dict(PARAMS, golden_cross_lookback=100)
        signals, metrics = detect_signals(df, params)
        assert "golden_cross" in signals
        assert metrics["uptrend"] == 1.0

    def test_old_golden_cross_not_detected_with_narrow_lookback(self):
        # V字の後に長い横ばいを足す → クロスは大昔なので lookback=5 では検知しない
        close = np.concatenate([v_shape(), np.full(150, 160.0)])
        df = make_ohlcv(close)
        signals, _ = detect_signals(df, PARAMS)
        assert "golden_cross" not in signals

    def test_breakout_52w_requires_volume(self):
        # 最終日に52週高値を大出来高で更新 → breakout_52w + volume_surge
        close = np.concatenate([np.linspace(100, 150, 300), [160.0]])
        volume = np.full(len(close), 1_000_000.0)
        volume[-1] = 5_000_000.0
        df = make_ohlcv(close, volume)
        signals, metrics = detect_signals(df, PARAMS)
        assert "breakout_52w" in signals
        assert "volume_surge" in signals
        assert metrics["pct_from_52w_high"] > 0

        # 同じ値動きでも出来高が平常なら breakout は成立しない
        df_low_vol = make_ohlcv(close)
        signals_low, _ = detect_signals(df_low_vol, PARAMS)
        assert "breakout_52w" not in signals_low
        assert "volume_surge" not in signals_low

    def test_pullback_in_uptrend(self):
        # 強い上昇トレンドの後、下げ優勢の揉み合いでRSIが調整圏へ → 押し目シグナル
        up = np.linspace(100, 200, 300)
        dip, price = [], 200.0
        for i in range(20):
            price *= 0.993 if i % 2 == 0 else 1.003
            dip.append(price)
        df = make_ohlcv(np.concatenate([up, dip]))
        signals, metrics = detect_signals(df, PARAMS)
        assert "pullback" in signals
        assert PARAMS["pullback_rsi_min"] <= metrics["rsi"] <= PARAMS["pullback_rsi_max"]
        assert metrics["uptrend"] == 1.0

    def test_downtrend_dip_is_not_pullback(self):
        # 下落トレンド中のRSI低下は押し目として扱わない
        df = make_ohlcv(np.linspace(200, 100, 300))
        signals, _ = detect_signals(df, PARAMS)
        assert "pullback" not in signals


class TestScanWatchlist:
    def test_only_signaled_tickers_returned(self):
        watchlist = pd.DataFrame({
            "ticker": ["UP.T", "FLAT.T", "MISSING.T"],
            "name": ["上昇", "横ばい", "データ無し"],
            "market": ["japan", "japan", "japan"],
        })
        close = np.concatenate([np.linspace(100, 150, 300), [160.0]])
        volume = np.full(len(close), 1_000_000.0)
        volume[-1] = 5_000_000.0
        prices = {
            "UP.T": make_ohlcv(close, volume),
            "FLAT.T": make_ohlcv(np.full(300, 100.0)),
        }
        results = scan_watchlist(prices, watchlist, PARAMS)
        assert [r["ticker"] for r in results] == ["UP.T"]
        assert results[0]["name"] == "上昇"
        assert "breakout_52w" in results[0]["signals"]
